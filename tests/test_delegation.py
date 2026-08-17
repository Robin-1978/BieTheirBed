from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from knoa_platform.agent_runtime.composition import (
    PERSONAL_LOCAL_CAPABILITIES,
    build_core_runtime,
)
from knoa_platform.agent_runtime.contracts import HealthStatus
from knoa_platform.agents import NodeAgentResolver
from knoa_platform.config import AppConfig


class _OfflineProvider:
    def __init__(self, model) -> None:
        self.model_alias = model.alias

    async def health_check(self) -> HealthStatus:
        return HealthStatus(healthy=True)

    def stream(self, request, cancellation):
        del request, cancellation

        async def empty_stream():
            if False:
                yield

        return empty_stream()


def _delegation_config(tmp_path: Path) -> AppConfig:
    base = AppConfig(
        fallback_enabled=False,
        runtime_root=str(tmp_path / "runtime"),
        working_directory=str(tmp_path),
        service_port=0,
    )
    agents = dict(base.node_agents)
    agents["codex"] = agents["codex"].model_copy(
        update={"enabled": True, "cwd": str(tmp_path)}
    )
    assistant = agents["knoa"]
    agents["knoa"] = assistant.model_copy(
        update={
            "delegation": assistant.delegation.model_copy(
                update={"max_parallel_children": 1}
            )
        }
    )
    return base.model_copy(update={"node_agents": agents})


@pytest.mark.asyncio
async def test_delegation_creates_one_governed_child_task_and_enforces_parallelism(
    tmp_path: Path,
) -> None:
    composition = build_core_runtime(
        _delegation_config(tmp_path),
        provider_factory=_OfflineProvider,
    )
    scope = composition.sessions.create(
        "personal:owner",
        activate=False,
        agent_id="knoa",
    )
    parent = await composition.task_service.create(
        scope,
        client_request_id="parent-request",
        goal="Coordinate delegated work",
        defer_start=True,
    )
    managed = composition.configuration.current().document
    resolver = NodeAgentResolver(
        managed.agents,
        config_revision_id=composition.configuration.current().revision_id,
    )
    parent_policy = resolver.resolve_policy(
        "knoa",
        invocation_kind="user",
        caller_id=scope.principal_id,
        principal_capabilities=frozenset(
            capability.value for capability in PERSONAL_LOCAL_CAPABILITIES
        ),
        available_tools=composition.capability_gateway.available_tool_names(
            PERSONAL_LOCAL_CAPABILITIES
        ),
        installed_skills=frozenset(
            package.manifest.id for package in composition.skills.packages
        ),
    )
    composition.invocation_policies.record(
        parent.task_id,
        scope.principal_id,
        scope.session_handle,
        parent_policy,
    )

    async def spawn_once():
        return await composition.delegations.spawn(
            scope,
            parent.task_id,
            target_agent_id="codex",
            goal="Inspect the repository",
            context={"focus": "architecture"},
            requested_capabilities=frozenset(),
            requested_tools=frozenset(),
            requested_skills=frozenset(),
            deadline_seconds=60,
            mode="detached",
            idempotency_key="delegate-once",
        )

    first, duplicate = await asyncio.gather(spawn_once(), spawn_once())

    assert duplicate.delegation_id == first.delegation_id
    assert duplicate.child_task_id == first.child_task_id
    assert first.depth == 1
    assert first.invocation_policy.delegation_max_depth == 0
    child = await composition.task_service.get(
        scope.principal_id,
        first.child_task_id,
    )
    assert child.origin.value == "agent"
    assert child.parent_task_id == parent.task_id
    assert child.agent_id == "codex"
    assert composition.invocation_policies.get(child.task_id).policy_digest == (
        first.invocation_policy_digest
    )

    with pytest.raises(PermissionError, match="parallel child limit"):
        await composition.delegations.spawn(
            scope,
            parent.task_id,
            target_agent_id="codex",
            goal="Start a second child",
            context={},
            requested_capabilities=frozenset(),
            requested_tools=frozenset(),
            requested_skills=frozenset(),
            deadline_seconds=60,
            mode="detached",
            idempotency_key="delegate-twice",
        )
