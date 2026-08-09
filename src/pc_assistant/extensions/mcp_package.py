"""Manually imported local MCP packages discovered below the runtime root."""
from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
import shutil
import stat
import uuid

import yaml

from pc_assistant.extensions.manager import ExtensionManager, ExtensionStatus
from pc_assistant.extensions.mcp import MCPServerProvider
from pc_assistant.extensions.models import MCP_SERVER_ID_PATTERN, MCPServerConfig


logger = logging.getLogger(__name__)

_MANIFEST_NAME = "mcp.yaml"
_MAX_MANIFEST_BYTES = 64 * 1024
_MAX_PACKAGE_FILES = 4096
_MAX_PACKAGE_FILE_BYTES = 32 * 1024 * 1024
_MAX_PACKAGE_BYTES = 128 * 1024 * 1024


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
) -> MCPServerConfig:
    root = Path(package_root).expanduser().resolve()
    if not root.is_dir():
        raise ValueError("MCP package root must be a directory")
    if validate_directory_id and not MCP_SERVER_ID_PATTERN.fullmatch(root.name):
        raise ValueError("MCP package directory must use a safe server ID")
    raw = yaml.safe_load(_read_manifest(root))
    if not isinstance(raw, dict):
        raise ValueError("MCP manifest must be a mapping")
    config = MCPServerConfig.model_validate(raw)
    if config.transport != "stdio":
        raise ValueError("Local MCP packages must use stdio transport")
    if not config.enabled:
        raise ValueError("Local MCP package must be explicitly enabled")
    cwd = _working_directory(root, config.working_directory)
    return config.model_copy(update={"working_directory": str(cwd)})


def load_mcp_package(package_root: str | Path) -> MCPServerConfig:
    """Load one installed data-only launch manifest without importing code."""

    return _load_mcp_package(package_root, validate_directory_id=True)


def build_mcp_package_providers(
    root: str | Path,
    *,
    excluded_ids: frozenset[str] = frozenset(),
) -> tuple[MCPServerProvider, ...]:
    """Discover safe package directories; each manifest loads in its own lifecycle."""

    resolved = Path(root).expanduser().resolve()
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
        providers.append(
            MCPServerProvider(
                server_id,
                config_loader=lambda package_root=package_root: load_mcp_package(
                    package_root
                ),
            )
        )
    return tuple(providers)


def _is_hidden_metadata(name: str) -> bool:
    return name.startswith(".")


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
        *,
        reserved_ids: frozenset[str] = frozenset(),
    ) -> None:
        self._package_root = Path(package_root).expanduser().resolve()
        self._staging_root = Path(staging_root).expanduser().resolve()
        self._manager = manager
        self._reserved_ids = reserved_ids
        self._import_lock = asyncio.Lock()

    async def import_local(self, source_path: str | Path, server_id: str) -> ExtensionStatus:
        normalized_id = server_id.strip()
        if not MCP_SERVER_ID_PATTERN.fullmatch(normalized_id):
            raise ValueError("MCP server ID must contain 1-24 safe characters")
        if normalized_id in self._reserved_ids:
            raise ValueError("MCP server ID is reserved by configured Core policy")
        source = Path(source_path).expanduser().resolve()
        async with self._import_lock:
            target = self._package_root / normalized_id
            if target.exists():
                raise ValueError("MCP package is already installed")
            await asyncio.to_thread(self._stage_and_install, source, target)
            provider = MCPServerProvider(
                normalized_id,
                config_loader=lambda: load_mcp_package(target),
            )
            try:
                return await self._manager.add_provider(provider)
            except BaseException:
                await asyncio.to_thread(shutil.rmtree, target, True)
                raise

    def _stage_and_install(self, source: Path, target: Path) -> None:
        if not source.is_dir():
            raise ValueError("MCP import source must be a directory")
        for managed_root in (self._package_root, self._staging_root):
            try:
                managed_root.relative_to(source)
            except ValueError:
                continue
            raise ValueError("MCP import source must not contain Knoa runtime roots")
        _load_mcp_package(source, validate_directory_id=False)
        directories, files = _package_layout(source)
        self._package_root.mkdir(parents=True, exist_ok=True)
        self._staging_root.mkdir(parents=True, exist_ok=True)
        stage_parent = self._staging_root / uuid.uuid4().hex
        stage = stage_parent / target.name
        stage.mkdir(parents=True, mode=0o700)
        try:
            for relative in directories:
                (stage / relative).mkdir(parents=True, exist_ok=True)
            for source_file, relative in files:
                destination = stage / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source_file, destination, follow_symlinks=False)
            load_mcp_package(stage)
            if target.exists():
                raise ValueError("MCP package is already installed")
            stage.replace(target)
        finally:
            if stage_parent.exists():
                shutil.rmtree(stage_parent, ignore_errors=True)
