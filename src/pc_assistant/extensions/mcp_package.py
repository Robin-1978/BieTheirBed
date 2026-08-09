"""Manually imported local MCP packages discovered below the runtime root."""
from __future__ import annotations

import logging
from pathlib import Path

import yaml

from pc_assistant.extensions.mcp import MCPServerProvider
from pc_assistant.extensions.models import MCP_SERVER_ID_PATTERN, MCPServerConfig


logger = logging.getLogger(__name__)

_MANIFEST_NAME = "mcp.yaml"
_MAX_MANIFEST_BYTES = 64 * 1024


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


def load_mcp_package(package_root: str | Path) -> MCPServerConfig:
    """Load one data-only launch manifest without importing package code."""

    root = Path(package_root).expanduser().resolve()
    if not root.is_dir():
        raise ValueError("MCP package root must be a directory")
    if not MCP_SERVER_ID_PATTERN.fullmatch(root.name):
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
