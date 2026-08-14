from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from knoa_platform.agent_runtime.config_control import PersistentConfigController
from knoa_platform.config import AppConfig
from knoa_platform.extensions.manager import ExtensionManager
from knoa_platform.extensions.mcp import (
    MCPPromptDefinition,
    MCPResourceCapabilities,
    MCPResourceDefinition,
    MCPResourceSnapshot,
    MCPToolDefinition,
)
from knoa_platform.extensions.mcp_onboarding import MCPOnboardingService
from knoa_platform.extensions.mcp_resource_tasks import MCPResourceTaskBridge
from knoa_platform.extensions.models import MCPResourceTaskConfig, MCPServerConfig
from knoa_platform.tools.registry import ToolRegistry


class _DiscoveryClient:
    def __init__(self) -> None:
        self.closed = False

    def set_notification_handler(self, _handler) -> None:
        pass

    async def start(self) -> None:
        pass

    async def list_tools(self):
        return (
            MCPToolDefinition(
                name="jira.get_issue",
                description="Read Jira",
                input_schema={"type": "object", "properties": {}},
                read_only_hint=True,
                open_world_hint=True,
            ),
            MCPToolDefinition(
                name="jira.add_comment",
                description="Write Jira",
                input_schema={"type": "object", "properties": {}},
                read_only_hint=False,
                open_world_hint=True,
            ),
            MCPToolDefinition(
                name="jira.ambiguous",
                description="Missing annotations",
                input_schema={"type": "object", "properties": {}},
            ),
        )

    async def list_prompts(self):
        return (MCPPromptDefinition("jira.analyze_issue", "Analyze Jira"),)

    def resource_capabilities(self):
        return MCPResourceCapabilities(available=True, subscribe=True)

    async def list_resources(self):
        return (
            MCPResourceDefinition(
                uri="jira://assigned-to-me",
                name="Assignments",
                description="",
                mime_type="application/json",
            ),
        )

    async def call_tool(self, _name, _arguments, elicitation_handler=None):
        del elicitation_handler
        raise AssertionError

    async def read_resource(self, _uri):
        return MCPResourceSnapshot(contents=())

    async def subscribe_resource(self, _uri):
        pass

    async def unsubscribe_resource(self, _uri):
        pass

    async def close(self) -> None:
        self.closed = True


class _ProviderClient(_DiscoveryClient):
    async def list_tools(self):
        return (await super().list_tools())[:1]


@pytest.mark.asyncio
async def test_onboarding_discovers_and_enables_only_annotated_read_tools(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import knoa_platform.extensions.mcp as mcp_module
    import knoa_platform.extensions.mcp_onboarding as onboarding_module

    discovery = _DiscoveryClient()
    provider_client = _ProviderClient()
    clients = iter((discovery, provider_client))
    monkeypatch.setattr(onboarding_module, "create_mcp_client", lambda _config: next(clients))
    monkeypatch.setattr(mcp_module, "create_mcp_client", lambda _config: next(clients))
    registry = ToolRegistry()
    manager = ExtensionManager(registry)
    await manager.start()
    path = tmp_path / "local.yaml"
    service = MCPOnboardingService(
        manager,
        PersistentConfigController(AppConfig(), path),
        MCPResourceTaskBridge((), object(), object(), object()),
    )

    result = await service.connect(
        "jira",
        MCPServerConfig.model_validate(
            {"enabled": True, "url": "https://jira-mcp.example.test/mcp"}
        ),
        frozenset({"jira.get_issue"}),
    )

    assert result.enabled_tools == ("jira.get_issue",)
    assert result.withheld_tools == ("jira.add_comment", "jira.ambiguous")
    assert result.prompts[0].name == "jira.analyze_issue"
    assert discovery.closed
    assert registry.list_tools() == ["mcp__jira__jira_get_issue"]
    saved = yaml.safe_load(path.read_text())
    assert set(saved["mcp_servers"]["jira"]["tools"]) == {"jira.get_issue"}
    assert "resource_tasks" not in saved["mcp_servers"]["jira"]
    await manager.stop()


@pytest.mark.asyncio
async def test_onboarding_rejects_legacy_resource_task_route(
    tmp_path: Path,
) -> None:
    path = tmp_path / "local.yaml"
    initial = AppConfig(
        mcp_servers={
            "jira": {
                "enabled": True,
                "url": "https://jira-mcp.example.test/mcp",
            }
        }
    )
    controller = PersistentConfigController(initial, path)

    class _Bridge:
        def __init__(self) -> None:
            self.calls = []

        def validate_route(self, _route) -> None:
            pass

        def add_route(self, provider, route_id, route) -> None:
            self.calls.append((provider, route_id, route))

    bridge = _Bridge()
    manager = ExtensionManager(ToolRegistry())
    provider = object()
    service = MCPOnboardingService(
        manager,
        controller,
        bridge,  # type: ignore[arg-type]
    )
    service._providers["jira"] = provider  # type: ignore[assignment]
    route = MCPResourceTaskConfig.model_validate(
        {
            "uri": "jira://assigned-to-me",
            "principal_id": "personal:owner",
            "session_handle": "session-a",
        }
    )

    with pytest.raises(ValueError, match="Task Definition launch policy"):
        await service.configure_resource_task("jira", "assigned", route)

    assert bridge.calls == []
    assert not path.exists()
