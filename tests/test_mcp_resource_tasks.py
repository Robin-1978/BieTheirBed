from __future__ import annotations

from types import SimpleNamespace

import pytest

from knoa_platform.agent_runtime.contracts import RuntimeScope
from knoa_platform.extensions.mcp import (
    MCPResourceCapabilities,
    MCPResourceContent,
    MCPResourceDefinition,
    MCPResourceSnapshot,
)
from knoa_platform.extensions.mcp_resource_tasks import MCPResourceTaskBridge
from knoa_platform.extensions.models import MCPServerConfig
from knoa_platform.tasks import TaskLaunchKind, TaskLaunchPolicy


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

    def isolated_task_scope(
        self,
        source: RuntimeScope,
        task_key: str,
    ) -> RuntimeScope:
        assert source.session_handle == "session-a"
        assert task_key.startswith("mcp-resource:")
        return RuntimeScope(
            principal_id=source.principal_id,
            session_handle="isolated-task-session",
        )


class _Tasks:
    def __init__(self, definitions=()) -> None:
        self.calls = []
        self.definitions = tuple(definitions)
        self.bindings = {
            definition.task_id: ("event", f"trigger-{definition.task_id}")
            for definition in self.definitions
        }

    async def list_definitions(self, principal_id: str, **kwargs):
        self.calls.append(("list", principal_id, kwargs))
        return tuple(
            definition
            for definition in self.definitions
            if definition.principal_id == principal_id
        )

    async def list_event_definitions(self, event_source: str, **kwargs):
        self.calls.append(("list_event", event_source, kwargs))
        return tuple(
            definition
            for definition in self.definitions
            if definition.launch_policy.event_source == event_source
        )

    async def launch_binding(self, principal_id: str, task_id: str):
        self.calls.append(("binding", principal_id, task_id))
        return self.bindings.get(task_id)

    async def bind_launch(
        self,
        principal_id: str,
        task_id: str,
        *,
        provider_kind: str,
        provider_id: str,
    ) -> None:
        self.calls.append(
            ("bind", principal_id, task_id, provider_kind, provider_id)
        )
        self.bindings[task_id] = (provider_kind, provider_id)

    async def unbind_launch(self, principal_id: str, task_id: str) -> None:
        self.calls.append(("unbind", principal_id, task_id))
        self.bindings.pop(task_id, None)


class _Triggers:
    def __init__(self) -> None:
        self.calls = []
        self.seen = set()
        self.created = []
        self.deleted = []

    async def create(self, scope: RuntimeScope, **kwargs):
        self.created.append((scope, kwargs))
        return SimpleNamespace(trigger_id="trigger-repaired")

    async def delete(self, principal_id: str, trigger_id: str) -> None:
        self.deleted.append((principal_id, trigger_id))

    async def get(self, principal_id: str, trigger_id: str):
        if trigger_id.startswith("missing-"):
            raise LookupError(trigger_id)
        return SimpleNamespace(trigger_id=trigger_id)

    async def receive(
        self,
        principal_id: str,
        trigger_id: str,
        *,
        external_event_id: str,
        payload,
    ):
        key = (trigger_id, external_event_id)
        if key not in self.seen:
            self.seen.add(key)
            self.calls.append(
                (principal_id, trigger_id, external_event_id, payload)
            )
        return SimpleNamespace(external_event_id=external_event_id)


def _definition(
    task_id: str = "task-a",
    *,
    source_config=None,
):
    return SimpleNamespace(
        task_id=task_id,
        principal_id="principal-a",
        session_handle="session-a",
        title="Analyze assigned issues",
        goal="Analyze the MCP Resource event.",
        tools_enabled=True,
        priority=0,
        launch_policy=TaskLaunchPolicy(
            kind=TaskLaunchKind.EVENT,
            event_source="mcp:jira",
            source_config=(
                {
                    "resource_uri_prefix": "jira://assigned-to-me/events",
                    "include_root": True,
                    "include_descendants": True,
                }
                if source_config is None
                else source_config
            ),
        ),
    )


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
async def test_resource_inventory_triggers_one_existing_event_task() -> None:
    root = "jira://assigned-to-me"
    event = "jira://assigned-to-me/events/assignment-1"
    outside = "jira://other/events/assignment-2"
    provider = _Provider((_resource(root), _resource(event), _resource(outside)))
    provider.snapshots[event] = _snapshot(event, "Analyze Jira issue PROJECT-1")
    tasks = _Tasks((_definition(),))
    triggers = _Triggers()
    bridge = MCPResourceTaskBridge((provider,), tasks, _Sessions(), triggers)

    await bridge.reconcile_once()
    await bridge.reconcile_once()

    assert len(triggers.calls) == 1
    principal_id, trigger_id, external_event_id, payload = triggers.calls[0]
    assert principal_id == "principal-a"
    assert trigger_id == "trigger-task-a"
    assert external_event_id.startswith("mcp-resource:")
    assert payload["server_id"] == "jira"
    assert payload["resource_uri"] == event
    assert payload["contents"][0]["text"] == "Analyze Jira issue PROJECT-1"
    assert outside not in provider.subscribed
    assert provider.subscribed == [event]


@pytest.mark.asyncio
async def test_invalid_session_is_retried_without_consuming_resource() -> None:
    event = "jira://assigned-to-me/events/assignment-1"
    provider = _Provider((_resource(event),))
    provider.snapshots[event] = _snapshot(event, "Analyze Jira issue PROJECT-1")
    sessions = _Sessions()
    sessions.available = False
    tasks = _Tasks((_definition(),))
    triggers = _Triggers()
    bridge = MCPResourceTaskBridge((provider,), tasks, sessions, triggers)

    await bridge.reconcile_once()
    sessions.available = True
    await bridge.reconcile_once()

    assert len(triggers.calls) == 1


@pytest.mark.asyncio
async def test_completed_inventory_unsubscribes_removed_resources() -> None:
    root = "jira://assigned-to-me"
    event = "jira://assigned-to-me/events/assignment-1"
    provider = _Provider((_resource(root), _resource(event)))
    provider.snapshots[event] = _snapshot(event, "Analyze Jira issue PROJECT-1")
    bridge = MCPResourceTaskBridge(
        (provider,),
        _Tasks((_definition(),)),
        _Sessions(),
        _Triggers(),
    )

    await bridge.reconcile_once()
    provider.resources = (_resource(root),)
    await bridge.reconcile_once()

    assert event in provider.unsubscribed
    assert root not in provider.unsubscribed


@pytest.mark.asyncio
async def test_reconciliation_tolerates_provider_removed_during_inventory() -> None:
    root = "jira://assigned-to-me"
    provider = _Provider((_resource(root),))
    bridge = MCPResourceTaskBridge(
        (provider,),
        _Tasks((_definition(),)),
        _Sessions(),
        _Triggers(),
    )

    async def list_and_remove():
        await bridge.remove_provider(provider)
        return provider.resources

    provider.list_resources = list_and_remove  # type: ignore[method-assign]

    await bridge.reconcile_once()

    assert "jira" not in bridge._providers


@pytest.mark.asyncio
async def test_unsafe_or_lookalike_resource_uri_is_not_authorized() -> None:
    valid = "jira://assigned-to-me/events/assignment-1"
    lookalike = "jira://assigned-to-me.evil/events/assignment-2"
    traversal = "jira://assigned-to-me/events/%2Fsecret"
    provider = _Provider((_resource(valid), _resource(lookalike), _resource(traversal)))
    provider.snapshots[valid] = _snapshot(valid, "Analyze Jira issue PROJECT-1")
    tasks = _Tasks((_definition(),))
    triggers = _Triggers()
    bridge = MCPResourceTaskBridge((provider,), tasks, _Sessions(), triggers)

    await bridge.reconcile_once()

    assert len(triggers.calls) == 1
    assert provider.subscribed == [valid]


@pytest.mark.asyncio
async def test_mutable_resource_content_digest_creates_a_new_trigger_event() -> None:
    event = "jira://assigned-to-me/events/assignment-1"
    provider = _Provider((_resource(event),))
    provider.snapshots[event] = _snapshot(event, "first revision")
    triggers = _Triggers()
    bridge = MCPResourceTaskBridge(
        (provider,),
        _Tasks((_definition(),)),
        _Sessions(),
        triggers,
    )

    await bridge.reconcile_once()
    provider.snapshots[event] = _snapshot(event, "second revision")
    await bridge.reconcile_once()

    assert len(triggers.calls) == 2
    assert triggers.calls[0][2] != triggers.calls[1][2]


@pytest.mark.asyncio
async def test_resource_without_matching_task_definition_is_not_delivered() -> None:
    event = "jira://assigned-to-me/events/assignment-1"
    provider = _Provider((_resource(event),))
    provider.snapshots[event] = _snapshot(event, "Analyze Jira issue PROJECT-1")
    triggers = _Triggers()
    bridge = MCPResourceTaskBridge(
        (provider,),
        _Tasks(
            (
                _definition(
                    source_config={"resource_uri": "jira://other/event"}
                ),
            )
        ),
        _Sessions(),
        triggers,
    )

    await bridge.reconcile_once()

    assert triggers.calls == []


@pytest.mark.asyncio
async def test_legacy_event_task_repairs_missing_trigger_binding() -> None:
    event = "jira://assigned-to-me/events/assignment-1"
    provider = _Provider((_resource(event),))
    provider.snapshots[event] = _snapshot(event, "Analyze Jira issue PROJECT-1")
    tasks = _Tasks((_definition(),))
    tasks.bindings.clear()
    triggers = _Triggers()
    bridge = MCPResourceTaskBridge(
        (provider,),
        tasks,
        _Sessions(),
        triggers,
    )

    await bridge.reconcile_once()

    assert tasks.bindings["task-a"] == ("event", "trigger-repaired")
    assert len(triggers.created) == 1
    assert len(triggers.calls) == 1


@pytest.mark.asyncio
async def test_stale_trigger_binding_is_replaced() -> None:
    event = "jira://assigned-to-me/events/assignment-1"
    provider = _Provider((_resource(event),))
    provider.snapshots[event] = _snapshot(event, "Analyze Jira issue PROJECT-1")
    tasks = _Tasks((_definition(),))
    tasks.bindings["task-a"] = ("event", "missing-trigger")
    triggers = _Triggers()
    bridge = MCPResourceTaskBridge((provider,), tasks, _Sessions(), triggers)

    await bridge.reconcile_once()

    assert ("unbind", "principal-a", "task-a") in tasks.calls
    assert tasks.bindings["task-a"] == ("event", "trigger-repaired")
    assert len(triggers.calls) == 1


@pytest.mark.asyncio
async def test_missing_binding_is_repaired_without_any_resource() -> None:
    provider = _Provider(())
    tasks = _Tasks((_definition(),))
    tasks.bindings.clear()
    bridge = MCPResourceTaskBridge((provider,), tasks, _Sessions(), _Triggers())

    await bridge.reconcile_once()

    assert tasks.bindings["task-a"] == ("event", "trigger-repaired")
    assert bridge.catalog() == ()


@pytest.mark.asyncio
async def test_legacy_server_routes_do_not_activate_event_delivery() -> None:
    event = "jira://assigned-to-me/events/assignment-1"
    provider = _Provider((_resource(event),))
    provider.snapshots[event] = _snapshot(event, "Analyze Jira issue PROJECT-1")
    triggers = _Triggers()
    bridge = MCPResourceTaskBridge((provider,), _Tasks(()), _Sessions(), triggers)

    await bridge.reconcile_once()

    assert triggers.calls == []
    assert provider.subscribed == []
    assert [(item.server_id, item.uri) for item in bridge.catalog()] == [
        ("jira", event)
    ]
