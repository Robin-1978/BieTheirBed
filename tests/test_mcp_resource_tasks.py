from __future__ import annotations

from types import SimpleNamespace

import pytest

from pc_assistant.agent_runtime.contracts import RuntimeScope
from pc_assistant.extensions.mcp import (
    MCPResourceCapabilities,
    MCPResourceContent,
    MCPResourceDefinition,
    MCPResourceSnapshot,
)
from pc_assistant.extensions.mcp_resource_tasks import MCPResourceTaskBridge
from pc_assistant.extensions.models import MCPServerConfig
from pc_assistant.tasks import TaskOrigin


class _Provider:
    def __init__(self, resources: tuple[MCPResourceDefinition, ...]) -> None:
        self.server_id = "jira"
        self.config = MCPServerConfig.model_validate(
            {
                "enabled": True,
                "url": "https://mcp.example.test/mcp",
                "resource_tasks": {
                    "assigned": {
                        "uri": "jira://assigned-to-me",
                        "principal_id": "principal-a",
                        "session_handle": "session-a",
                        "priority": 4,
                    }
                },
            }
        )
        self.resources = resources
        self.snapshots: dict[str, MCPResourceSnapshot] = {}
        self.listeners = []
        self.subscribed: list[str] = []
        self.unsubscribed: list[str] = []

    def add_notification_listener(self, listener) -> None:
        self.listeners.append(listener)

    def resource_capabilities(self) -> MCPResourceCapabilities:
        return MCPResourceCapabilities(
            available=True, subscribe=True, list_changed=True
        )

    async def list_resources(self) -> tuple[MCPResourceDefinition, ...]:
        return self.resources

    async def read_resource(self, uri: str) -> MCPResourceSnapshot:
        return self.snapshots[uri]

    async def subscribe_resource(self, uri: str) -> None:
        self.subscribed.append(uri)

    async def unsubscribe_resource(self, uri: str) -> None:
        self.unsubscribed.append(uri)


class _Sessions:
    def __init__(self) -> None:
        self.available = True

    def resolve(self, principal_id: str, session_handle: str) -> RuntimeScope:
        if not self.available:
            raise LookupError("missing session")
        assert principal_id == "principal-a"
        assert session_handle == "session-a"
        return RuntimeScope(principal_id=principal_id, session_handle=session_handle)


class _Tasks:
    def __init__(self) -> None:
        self.calls = []

    async def create(self, scope: RuntimeScope, **kwargs):
        self.calls.append((scope, kwargs))
        return SimpleNamespace(task_id="task-a")


def _resource(uri: str) -> MCPResourceDefinition:
    return MCPResourceDefinition(
        uri=uri,
        name=uri.rsplit("/", 1)[-1],
        description="",
        mime_type="text/markdown",
    )


def _snapshot(uri: str, text: str) -> MCPResourceSnapshot:
    return MCPResourceSnapshot(
        contents=(
            MCPResourceContent(
                uri=uri,
                mime_type="text/markdown",
                text=text,
            ),
        )
    )


@pytest.mark.asyncio
async def test_resource_inventory_creates_one_owned_event_task() -> None:
    root = "jira://assigned-to-me"
    event = "jira://assigned-to-me/events/assignment-1"
    outside = "jira://other/events/assignment-2"
    provider = _Provider((_resource(root), _resource(event), _resource(outside)))
    provider.snapshots[event] = _snapshot(event, "Analyze Jira issue PROJECT-1")
    tasks = _Tasks()
    bridge = MCPResourceTaskBridge((provider,), tasks, _Sessions())

    await bridge.reconcile_once()
    await bridge.reconcile_once()

    assert len(tasks.calls) == 1
    scope, request = tasks.calls[0]
    assert scope == RuntimeScope(principal_id="principal-a", session_handle="session-a")
    assert request["origin"] is TaskOrigin.EVENT
    assert request["priority"] == 4
    assert "Analyze Jira issue PROJECT-1" in request["goal"]
    assert outside not in provider.subscribed
    assert set(provider.subscribed) == {root, event}


@pytest.mark.asyncio
async def test_invalid_session_is_retried_without_consuming_resource() -> None:
    event = "jira://assigned-to-me/events/assignment-1"
    provider = _Provider((_resource(event),))
    provider.snapshots[event] = _snapshot(event, "Analyze Jira issue PROJECT-1")
    sessions = _Sessions()
    sessions.available = False
    tasks = _Tasks()
    bridge = MCPResourceTaskBridge((provider,), tasks, sessions)

    await bridge.reconcile_once()
    sessions.available = True
    await bridge.reconcile_once()

    assert len(tasks.calls) == 1


@pytest.mark.asyncio
async def test_completed_inventory_unsubscribes_removed_resources() -> None:
    root = "jira://assigned-to-me"
    event = "jira://assigned-to-me/events/assignment-1"
    provider = _Provider((_resource(root), _resource(event)))
    provider.snapshots[event] = _snapshot(event, "Analyze Jira issue PROJECT-1")
    bridge = MCPResourceTaskBridge((provider,), _Tasks(), _Sessions())

    await bridge.reconcile_once()
    provider.resources = (_resource(root),)
    await bridge.reconcile_once()

    assert event in provider.unsubscribed
    assert root not in provider.unsubscribed


@pytest.mark.asyncio
async def test_unsafe_or_lookalike_resource_uri_is_not_authorized() -> None:
    valid = "jira://assigned-to-me/events/assignment-1"
    lookalike = "jira://assigned-to-me.evil/events/assignment-2"
    traversal = "jira://assigned-to-me/events/%2Fsecret"
    provider = _Provider((_resource(valid), _resource(lookalike), _resource(traversal)))
    provider.snapshots[valid] = _snapshot(valid, "Analyze Jira issue PROJECT-1")
    tasks = _Tasks()
    bridge = MCPResourceTaskBridge((provider,), tasks, _Sessions())

    await bridge.reconcile_once()

    assert len(tasks.calls) == 1
    assert provider.subscribed == [valid]
