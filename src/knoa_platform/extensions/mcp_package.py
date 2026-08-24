"""Manually imported local MCP packages discovered below the runtime root."""
from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
import shutil
import stat
import sys
import uuid

import yaml

from knoa_platform.extensions.manager import (
    ExtensionManager,
    ExtensionState,
    ExtensionStatus,
)
from knoa_platform.extensions.mcp import MCPServerProvider
from knoa_platform.extensions.mcp_secrets import mcp_private_environment_loader
from knoa_platform.extensions.mcp_resource_tasks import MCPResourceTaskBridge
from knoa_platform.extensions.models import (
    MCP_SERVER_ID_PATTERN,
    MCPResourceTaskConfig,
    MCPServerConfig,
)


logger = logging.getLogger(__name__)

_MANIFEST_NAME = "mcp.yaml"
_DEPLOYMENT_NAME = ".knoa-deployment.yaml"
_MAX_MANIFEST_BYTES = 64 * 1024
_MAX_PACKAGE_FILES = 4096
_MAX_PACKAGE_FILE_BYTES = 32 * 1024 * 1024
_MAX_PACKAGE_BYTES = 128 * 1024 * 1024
_KNOA_PYTHON_COMMAND = "@knoa-python"


def _read_manifest(package_root: Path) -> str:
    manifest_path = (package_root / _MANIFEST_NAME).resolve()
    try:
        manifest_path.relative_to(package_root)
    except ValueError as exc:
        raise ValueError("MCP manifest escapes the package root") from exc
    if not manifest_path.is_file():
        raise ValueError(f"MCP package requires {_MANIFEST_NAME}")
    with manifest_path.open("rb") as stream:
        data = stream.read(_MAX_MANIFEST_BYTES + 1)
    if len(data) > _MAX_MANIFEST_BYTES:
        raise ValueError(f"MCP manifest exceeds {_MAX_MANIFEST_BYTES} bytes")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("MCP manifest must be UTF-8 text") from exc


def _working_directory(package_root: Path, configured: str) -> Path:
    relative = Path(configured or ".")
    if relative.is_absolute():
        raise ValueError("Local MCP working_directory must be relative")
    resolved = (package_root / relative).resolve()
    try:
        resolved.relative_to(package_root)
    except ValueError as exc:
        raise ValueError("MCP working_directory escapes the package root") from exc
    if not resolved.is_dir():
        raise ValueError("MCP working_directory must be an existing directory")
    return resolved


def _load_mcp_package(
    package_root: str | Path,
    *,
    validate_directory_id: bool,
    include_deployment: bool = True,
) -> MCPServerConfig:
    root = Path(package_root).expanduser().resolve()
    if not root.is_dir():
        raise ValueError("MCP package root must be a directory")
    if validate_directory_id and not MCP_SERVER_ID_PATTERN.fullmatch(root.name):
        raise ValueError("MCP package directory must use a safe server ID")
    raw = yaml.safe_load(_read_manifest(root))
    if not isinstance(raw, dict):
        raise ValueError("MCP manifest must be a mapping")
    if raw.get("resource_tasks"):
        raise ValueError(
            "MCP package manifests must not embed principal-owned Resource Task routes"
        )
    if include_deployment:
        raw = {**raw, "resource_tasks": _read_deployment(root)}
    config = MCPServerConfig.model_validate(raw)
    if config.transport != "stdio":
        raise ValueError("Local MCP packages must use stdio transport")
    if not config.enabled:
        raise ValueError("Local MCP package must be explicitly enabled")
    if config.command == _KNOA_PYTHON_COMMAND:
        config = config.model_copy(update={"command": sys.executable})
    cwd = _working_directory(root, config.working_directory)
    return config.model_copy(update={"working_directory": str(cwd)})


def load_mcp_package(package_root: str | Path) -> MCPServerConfig:
    """Load one installed data-only launch manifest without importing code."""

    return _load_mcp_package(package_root, validate_directory_id=True)


def _read_deployment(package_root: Path) -> dict[str, MCPResourceTaskConfig]:
    path = package_root / _DEPLOYMENT_NAME
    if not path.exists():
        return {}
    if path.is_symlink() or not path.is_file():
        raise ValueError("MCP package deployment state must be a regular file")
    data = path.read_bytes()
    if len(data) > _MAX_MANIFEST_BYTES:
        raise ValueError("MCP package deployment state is too large")
    try:
        raw = yaml.safe_load(data.decode("utf-8"))
    except (UnicodeDecodeError, yaml.YAMLError) as exc:
        raise ValueError("MCP package deployment state is invalid") from exc
    if raw is None:
        return {}
    if not isinstance(raw, dict) or set(raw) != {"resource_tasks"}:
        raise ValueError("MCP package deployment state has unsupported fields")
    routes = raw.get("resource_tasks")
    if not isinstance(routes, dict):
        raise ValueError("MCP package Resource Task routes must be a mapping")
    return {
        str(route_id): MCPResourceTaskConfig.model_validate(route)
        for route_id, route in routes.items()
    }


def _write_deployment(
    package_root: Path,
    routes: dict[str, MCPResourceTaskConfig],
) -> None:
    path = package_root / _DEPLOYMENT_NAME
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(
            yaml.safe_dump(
                {
                    "resource_tasks": {
                        route_id: route.model_dump(
                            mode="json",
                            exclude_defaults=True,
                        )
                        for route_id, route in routes.items()
                    }
                },
                allow_unicode=True,
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        temporary.chmod(0o600)
        temporary.replace(path)
        path.chmod(0o600)
    finally:
        temporary.unlink(missing_ok=True)


def build_mcp_package_providers(
    root: str | Path,
    *,
    excluded_ids: frozenset[str] = frozenset(),
    secret_root: str | Path | None = None,
) -> tuple[MCPServerProvider, ...]:
    """Discover safe package directories; each manifest loads in its own lifecycle."""

    resolved = Path(root).expanduser().resolve()
    resolved_secret_root = (
        Path(secret_root).expanduser().resolve()
        if secret_root is not None
        else None
    )
    if not resolved.is_dir():
        return ()
    providers: list[MCPServerProvider] = []
    for package_root in sorted(path for path in resolved.iterdir() if path.is_dir()):
        server_id = package_root.name
        if not MCP_SERVER_ID_PATTERN.fullmatch(server_id):
            logger.warning("Ignoring MCP package with unsafe directory name: %s", package_root)
            continue
        if server_id in excluded_ids:
            logger.warning(
                "Ignoring local MCP package shadowed by configured server: %s",
                server_id,
            )
            continue
        # A half-written stdio package (for example while an import is being
        # replaced) is not an executable capability. Ignore it until the next
        # restart instead of registering a permanent failed extension.
        try:
            manifest = yaml.safe_load(_read_manifest(package_root))
        except (OSError, ValueError, yaml.YAMLError) as exc:
            logger.warning("Ignoring unreadable local MCP package %s: %s", package_root, exc)
            continue
        if (
            isinstance(manifest, dict)
            and manifest.get("transport", "stdio") == "stdio"
            and not str(manifest.get("command", "")).strip()
        ):
            logger.warning("Ignoring incomplete local MCP package without command: %s", package_root)
            continue
        providers.append(
            MCPServerProvider(
                server_id,
                config_loader=lambda package_root=package_root: load_mcp_package(
                    package_root
                ),
                private_environment_loader=mcp_private_environment_loader(
                    resolved_secret_root,
                    server_id,
                ),
            )
        )
    return tuple(providers)


def _is_hidden_metadata(name: str) -> bool:
    return (
        name.startswith(".")
        or name == "__pycache__"
        or name.endswith((".pyc", ".pyo"))
    )


def _package_layout(
    source: Path,
) -> tuple[tuple[Path, ...], tuple[tuple[Path, Path], ...]]:
    relative_directories: list[Path] = []
    files: list[tuple[Path, Path]] = []
    total_bytes = 0
    for current, directories, names in os.walk(source, followlinks=False):
        current_path = Path(current)
        kept_directories: list[str] = []
        for name in directories:
            candidate = current_path / name
            if _is_hidden_metadata(name):
                continue
            if candidate.is_symlink():
                continue
            kept_directories.append(name)
            relative_directories.append(candidate.relative_to(source))
        directories[:] = kept_directories
        for name in names:
            if _is_hidden_metadata(name):
                continue
            candidate = current_path / name
            metadata = candidate.lstat()
            if stat.S_ISLNK(metadata.st_mode):
                continue
            if not stat.S_ISREG(metadata.st_mode):
                raise ValueError("MCP packages may contain only regular files")
            if metadata.st_size > _MAX_PACKAGE_FILE_BYTES:
                raise ValueError("MCP package contains an oversized file")
            total_bytes += metadata.st_size
            if total_bytes > _MAX_PACKAGE_BYTES:
                raise ValueError("MCP package exceeds the total size limit")
            files.append((candidate, candidate.relative_to(source)))
            if len(files) > _MAX_PACKAGE_FILES:
                raise ValueError("MCP package contains too many files")
    return tuple(relative_directories), tuple(files)


class MCPPackageService:
    """Atomically import validated local packages and activate their provider."""

    def __init__(
        self,
        package_root: str | Path,
        staging_root: str | Path,
        manager: ExtensionManager,
        resource_tasks: MCPResourceTaskBridge,
        providers: tuple[MCPServerProvider, ...] = (),
        *,
        reserved_ids: frozenset[str] = frozenset(),
        secret_root: str | Path | None = None,
    ) -> None:
        self._package_root = Path(package_root).expanduser().resolve()
        self._staging_root = Path(staging_root).expanduser().resolve()
        self._manager = manager
        self._resource_tasks = resource_tasks
        self._providers = {provider.server_id: provider for provider in providers}
        self._reserved_ids = reserved_ids
        self._secret_root = (
            Path(secret_root).expanduser().resolve()
            if secret_root is not None
            else None
        )
        self._import_lock = asyncio.Lock()

    async def deploy_local(
        self,
        source_path: str | Path,
        server_id: str,
        *,
        route: tuple[str, MCPResourceTaskConfig] | None = None,
    ) -> tuple[str, ExtensionStatus]:
        normalized_id = server_id.strip()
        if not MCP_SERVER_ID_PATTERN.fullmatch(normalized_id):
            raise ValueError("MCP server ID must contain 1-24 safe characters")
        if normalized_id in self._reserved_ids:
            raise ValueError("MCP server ID is reserved by configured Core policy")
        source = Path(source_path).expanduser().resolve()
        async with self._import_lock:
            target = self._package_root / normalized_id
            stage_parent, stage = await asyncio.to_thread(
                self._stage_snapshot,
                source,
                normalized_id,
            )
            try:
                await asyncio.to_thread(
                    self._prepare_deployment,
                    target,
                    stage,
                    route,
                )
                action = "updated" if target.exists() else "installed"
                status = await self._activate_stage(normalized_id, target, stage)
                return action, status
            finally:
                await asyncio.to_thread(shutil.rmtree, stage_parent, True)

    def _stage_snapshot(self, source: Path, server_id: str) -> tuple[Path, Path]:
        if not source.is_dir():
            raise ValueError("MCP import source must be a directory")
        for managed_root in (self._package_root, self._staging_root):
            try:
                managed_root.relative_to(source)
            except ValueError:
                continue
            raise ValueError("MCP import source must not contain Knoa runtime roots")
        _load_mcp_package(
            source,
            validate_directory_id=False,
            include_deployment=False,
        )
        directories, files = _package_layout(source)
        self._package_root.mkdir(parents=True, exist_ok=True)
        self._staging_root.mkdir(parents=True, exist_ok=True)
        stage_parent = self._staging_root / uuid.uuid4().hex
        stage = stage_parent / server_id
        stage.mkdir(parents=True, mode=0o700)
        try:
            for relative in directories:
                (stage / relative).mkdir(parents=True, exist_ok=True)
            for source_file, relative in files:
                destination = stage / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source_file, destination, follow_symlinks=False)
            _load_mcp_package(
                stage,
                validate_directory_id=False,
                include_deployment=False,
            )
            return stage_parent, stage
        except BaseException:
            shutil.rmtree(stage_parent, ignore_errors=True)
            raise

    @staticmethod
    def _prepare_deployment(
        target: Path,
        stage: Path,
        route: tuple[str, MCPResourceTaskConfig] | None,
    ) -> None:
        routes = _read_deployment(target) if target.exists() else {}
        if route is not None:
            route_id, config = route
            routes[route_id] = config
        if routes:
            _write_deployment(stage, routes)
        load_mcp_package(stage)

    async def _activate_stage(
        self,
        server_id: str,
        target: Path,
        stage: Path,
    ) -> ExtensionStatus:
        old_provider = self._providers.get(server_id)
        backup = stage.parent / f"{server_id}.previous"
        if old_provider is not None:
            await self._resource_tasks.remove_provider(old_provider)
            await self._manager.remove_provider(old_provider)
        if target.exists():
            target.replace(backup)
        stage.replace(target)
        provider = MCPServerProvider(
            server_id,
            config_loader=lambda: load_mcp_package(target),
            private_environment_loader=mcp_private_environment_loader(
                self._secret_root,
                server_id,
            ),
        )
        try:
            status = await self._manager.add_provider(provider)
            if status.state is not ExtensionState.RUNNING:
                raise ValueError(status.detail or "MCP package could not be activated")
            self._resource_tasks.add_provider(provider)
        except BaseException:
            await self._resource_tasks.remove_provider(provider)
            await self._manager.remove_provider(provider)
            shutil.rmtree(target, ignore_errors=True)
            if backup.exists():
                backup.replace(target)
                restored = MCPServerProvider(
                    server_id,
                    config_loader=lambda: load_mcp_package(target),
                    private_environment_loader=mcp_private_environment_loader(
                        self._secret_root,
                        server_id,
                    ),
                )
                restored_status = await self._manager.add_provider(restored)
                if restored_status.state is ExtensionState.RUNNING:
                    self._resource_tasks.add_provider(restored)
                    self._providers[server_id] = restored
            raise
        shutil.rmtree(backup, ignore_errors=True)
        self._providers[server_id] = provider
        return status
