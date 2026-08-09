from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from pc_assistant.extensions import ExtensionManager, ExtensionState
from pc_assistant.extensions.mcp_package import (
    build_mcp_package_providers,
    load_mcp_package,
)
from pc_assistant.tools.registry import ToolRegistry


def _write_package(root: Path, server_id: str, **updates) -> Path:
    package = root / server_id
    package.mkdir(parents=True)
    manifest = {
        "enabled": True,
        "transport": "stdio",
        "command": "python",
        "args": ["-m", "monitor", "mcp"],
        "working_directory": ".",
        "tools": {
            "monitor.list_observations": {
                "effect": "read_only",
                "risk": "low",
                "capabilities": [],
            }
        },
    }
    manifest.update(updates)
    (package / "mcp.yaml").write_text(
        yaml.safe_dump(manifest, sort_keys=False),
        encoding="utf-8",
    )
    return package


def test_local_mcp_package_loads_and_resolves_cwd(tmp_path: Path) -> None:
    package = _write_package(tmp_path, "monitor")

    config = load_mcp_package(package)

    assert config.transport == "stdio"
    assert config.working_directory == str(package.resolve())
    assert "monitor.list_observations" in config.tools


def test_local_mcp_package_confines_manifest_and_working_directory(
    tmp_path: Path,
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    escaped = _write_package(tmp_path, "escaped", working_directory="../outside")
    linked = tmp_path / "linked"
    linked.mkdir()
    (linked / "mcp.yaml").symlink_to(escaped / "mcp.yaml")

    with pytest.raises(ValueError, match="working_directory escapes"):
        load_mcp_package(escaped)
    with pytest.raises(ValueError, match="manifest escapes"):
        load_mcp_package(linked)


def test_configured_server_takes_precedence_over_local_package(tmp_path: Path) -> None:
    _write_package(tmp_path, "monitor")

    providers = build_mcp_package_providers(
        tmp_path,
        excluded_ids=frozenset({"monitor"}),
    )

    assert providers == ()


@pytest.mark.asyncio
async def test_invalid_local_package_is_extension_isolated(tmp_path: Path) -> None:
    _write_package(
        tmp_path,
        "broken",
        transport="streamable_http",
        url="https://x",
        command="",
        args=[],
        working_directory="",
    )
    providers = build_mcp_package_providers(tmp_path)
    manager = ExtensionManager(ToolRegistry(), providers)

    await manager.start()

    assert len(manager.statuses) == 1
    assert manager.statuses[0].state is ExtensionState.FAILED
    assert "Local MCP packages must use stdio" in manager.statuses[0].detail
