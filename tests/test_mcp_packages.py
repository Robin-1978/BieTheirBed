from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from knoa_platform.extensions import (
    ExtensionManager,
    ExtensionState,
    ExtensionStatus,
)
from knoa_platform.extensions.mcp_package import (
    MCPPackageService,
    build_mcp_package_providers,
    load_mcp_package,
)
from knoa_platform.extensions.models import MCPResourceTaskConfig
from knoa_platform.tools.registry import ToolRegistry


class _ResourceTasks:
    def __init__(self) -> None:
        self.added = []
        self.removed = []

    def add_provider(self, provider) -> None:
        self.added.append(provider)

    async def remove_provider(self, provider) -> None:
        self.removed.append(provider)


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


@pytest.mark.asyncio
async def test_agent_import_copies_safe_snapshot_and_activates_provider(
    tmp_path: Path,
) -> None:
    source = _write_package(
        tmp_path / "sources",
        "monitor.source",
        command="command-that-does-not-exist-knoa-test",
        args=[],
    )
    (source / ".env").write_text("SECRET=must-not-copy\n", encoding="utf-8")
    (source / ".git").mkdir()
    (source / ".git" / "config").write_text("private", encoding="utf-8")
    (source / "__pycache__").mkdir()
    (source / "__pycache__" / "module.pyc").write_bytes(b"cache")
    (source / "empty").mkdir()
    registry = ToolRegistry()
    manager = ExtensionManager(registry)
    await manager.start()
    service = MCPPackageService(
        tmp_path / "runtime" / "mcp",
        tmp_path / "runtime" / "cache" / "mcp-imports",
        manager,
        _ResourceTasks(),
    )

    with pytest.raises(ValueError, match="could not be activated|FileNotFoundError"):
        await service.deploy_local(source, "monitor")

    installed = tmp_path / "runtime" / "mcp" / "monitor"
    assert not installed.exists()
    await manager.stop()


@pytest.mark.asyncio
async def test_agent_import_omits_symlinks_and_rejects_reserved_ids(
    tmp_path: Path,
) -> None:
    source = _write_package(tmp_path / "sources", "source")
    outside = tmp_path / "outside.txt"
    outside.write_text("outside", encoding="utf-8")
    (source / "linked.txt").symlink_to(outside)
    manager = ExtensionManager(ToolRegistry())
    await manager.start()
    service = MCPPackageService(
        tmp_path / "runtime" / "mcp",
        tmp_path / "runtime" / "cache" / "mcp-imports",
        manager,
        _ResourceTasks(),
        reserved_ids=frozenset({"configured"}),
    )

    with pytest.raises(ValueError, match="could not be activated|Connection closed"):
        await service.deploy_local(source, "linked")
    with pytest.raises(ValueError, match="reserved"):
        await service.deploy_local(source, "configured")

    installed = tmp_path / "runtime" / "mcp" / "linked"
    assert not installed.exists()
    await manager.stop()


def test_installed_package_loads_private_resource_task_deployment(
    tmp_path: Path,
) -> None:
    package = _write_package(tmp_path, "jira")
    deployment = package / ".knoa-deployment.yaml"
    deployment.write_text(
        yaml.safe_dump(
            {
                "resource_tasks": {
                    "assigned": {
                        "uri": "jira://assigned-to-me",
                        "principal_id": "personal:owner",
                        "session_handle": "session-a",
                        "priority": 4,
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    config = load_mcp_package(package)

    assert config.resource_tasks["assigned"].session_handle == "session-a"


@pytest.mark.asyncio
async def test_deploy_update_preserves_resource_task_and_replaces_package(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _write_package(tmp_path / "sources", "jira-source")
    (source / "version.txt").write_text("one", encoding="utf-8")
    manager = ExtensionManager(ToolRegistry())
    manager._running = True
    resource_tasks = _ResourceTasks()
    service = MCPPackageService(
        tmp_path / "runtime" / "mcp",
        tmp_path / "runtime" / "cache" / "mcp-imports",
        manager,
        resource_tasks,  # type: ignore[arg-type]
    )

    async def running(provider):
        manager._providers.append(provider)
        manager._statuses[provider.descriptor] = ExtensionStatus(
            provider.descriptor,
            ExtensionState.RUNNING,
        )
        return manager._statuses[provider.descriptor]

    monkeypatch.setattr(manager, "add_provider", running)
    route = MCPResourceTaskConfig.model_validate(
        {
            "uri": "jira://assigned-to-me",
            "principal_id": "personal:owner",
            "session_handle": "session-a",
        }
    )
    installed, _ = await service.deploy_local(
        source,
        "jira",
        route=("assigned", route),
    )
    (source / "version.txt").write_text("two", encoding="utf-8")
    updated, _ = await service.deploy_local(source, "jira")

    target = tmp_path / "runtime" / "mcp" / "jira"
    config = load_mcp_package(target)
    assert installed == "installed"
    assert updated == "updated"
    assert (target / "version.txt").read_text(encoding="utf-8") == "two"
    assert config.resource_tasks["assigned"] == route


@pytest.mark.asyncio
async def test_failed_update_restores_previous_running_package(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _write_package(tmp_path / "sources", "jira-source")
    (source / "version.txt").write_text("stable", encoding="utf-8")
    manager = ExtensionManager(ToolRegistry())
    manager._running = True
    resource_tasks = _ResourceTasks()
    service = MCPPackageService(
        tmp_path / "runtime" / "mcp",
        tmp_path / "runtime" / "cache" / "mcp-imports",
        manager,
        resource_tasks,  # type: ignore[arg-type]
    )
    starts = 0

    async def add_provider(provider):
        nonlocal starts
        starts += 1
        manager._providers.append(provider)
        state = ExtensionState.FAILED if starts == 2 else ExtensionState.RUNNING
        status = ExtensionStatus(
            provider.descriptor,
            state,
            detail="new package failed" if state is ExtensionState.FAILED else "",
        )
        manager._statuses[provider.descriptor] = status
        return status

    monkeypatch.setattr(manager, "add_provider", add_provider)
    await service.deploy_local(source, "jira")
    (source / "version.txt").write_text("broken", encoding="utf-8")

    with pytest.raises(ValueError, match="new package failed"):
        await service.deploy_local(source, "jira")

    target = tmp_path / "runtime" / "mcp" / "jira"
    assert (target / "version.txt").read_text(encoding="utf-8") == "stable"
    assert service._providers["jira"] is resource_tasks.added[-1]
