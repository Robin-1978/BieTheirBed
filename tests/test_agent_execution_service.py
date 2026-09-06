from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from knoa_agent import ContextCheckpointRepository, KnoaAgentRuntime
from knoa_platform.agent_runtime.contracts import ArtifactAttachment
from knoa_platform.agent_runtime.model_step import ProviderChunk
from knoa_platform.agent_runtime.session_store import RuntimeSessionRepository
from knoa_platform.agent_runtime.tool_step import ToolArgumentPolicy, ToolStep
from knoa_platform.agents import (
    AgentExecutionService,
    AgentManager,
    AgentSessionBindingRepository,
    ExecuteAgentTurn,
    ModelBindingSpec,
    NodeAgent,
    NodeAgentCatalog,
    NodeAgentResolver,
)
from knoa_platform.artifacts import ArtifactStore
from knoa_platform.capabilities import CapabilityGateway, GatewayMCPConnector
from knoa_platform.tools.registry import ToolRegistry


class Provider:
    def __init__(self) -> None:
        self.requests = []

    def stream(self, request, cancellation):
        del cancellation

        async def iterate():
            self.requests.append(request)
            yield ProviderChunk(content_delta="done")
            yield ProviderChunk(finish_reason="stop", terminal=True)

        return iterate()


class SerialProvider:
    def __init__(self) -> None:
        self.requests = []
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.active = 0
        self.max_active = 0

    def stream(self, request, cancellation):
        del cancellation

        async def iterate():
            self.requests.append(request)
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            self.started.set()
            try:
                await self.release.wait()
                yield ProviderChunk(content_delta="done")
                yield ProviderChunk(finish_reason="stop", terminal=True)
            finally:
                self.active -= 1

        return iterate()


async def healthy():
    return type("Health", (), {"healthy": True, "detail": "ok"})()


def resolver() -> NodeAgentResolver:
    return NodeAgentResolver(
        NodeAgentCatalog(
            agents={
                "knoa": NodeAgent(
                    kind="knoa",
                    display_name="Knoa Agent",
                    instructions="system",
                    visibility="user",
                    model_binding=ModelBindingSpec(
                        ownership="platform",
                        model="main",
                    ),
                    allowed_platform_tools=frozenset({"*"}),
                    platform_capability_ceiling=frozenset({"*"}),
                )
            },
            default_agent="knoa",
        )
    )


@pytest.mark.asyncio
async def test_execution_service_persists_binding_and_passes_artifact_by_mcp(
    tmp_path: Path,
) -> None:
    database = tmp_path / "platform.db"
    sessions = RuntimeSessionRepository(database, handle_factory=lambda: "session-a")
    scope = sessions.create("principal-a")
    artifacts = ArtifactStore(tmp_path / "attachments", db_path=database)
    artifact = artifacts.put_data_url(
        scope.session_handle,
        "data:text/plain;base64,aGVsbG8=",
        name="issue.log",
    )
    registry = ToolRegistry()
    gateway = CapabilityGateway(
        registry,
        ToolStep(registry, ToolArgumentPolicy(tmp_path)),
        artifacts,
    )
    provider = Provider()
    runtime = KnoaAgentRuntime(
        provider,
        ContextCheckpointRepository(
            tmp_path / "agent" / "context.db",
            session_id_factory=lambda: "agent-session-a",
        ),
        GatewayMCPConnector(gateway),
        system_prompt="system",
        health_probe=healthy,
        turn_id_factory=lambda: "runtime-turn-a",
    )
    bindings = AgentSessionBindingRepository(database)
    execution = AgentExecutionService(
        AgentManager({"knoa": runtime}),
        bindings,
        gateway,
        artifacts,
        capabilities_for=lambda _scope: frozenset(),
        resolver_for=resolver,
    )

    events = [
        event
        async for event in execution.execute_turn(
            ExecuteAgentTurn(
                scope=scope,
                turn_id="turn-a",
                client_request_id="request-a",
                input="inspect",
                attachments=(
                    ArtifactAttachment(artifact_id=artifact["artifact_id"]),
                ),
                tools_enabled=True,
                cancellation=asyncio.Event(),
            )
        )
    ]

    binding = bindings.get(scope)
    assert binding is not None
    assert binding.agent_id == "knoa"
    assert binding.runtime_session_ref == "agent-session-a"
    assert [event.event_type for event in events] == [
        "assistant_delta",
        "usage_reported",
        "turn_finished",
    ]
    assert "hello" in str(provider.requests[0].messages)
    assert "knoa-artifact://" not in str(provider.requests[0].messages)


@pytest.mark.asyncio
async def test_execution_service_rejects_agent_switch_after_session_binding(
    tmp_path: Path,
) -> None:
    database = tmp_path / "platform.db"
    sessions = RuntimeSessionRepository(database, handle_factory=lambda: "session-a")
    scope = sessions.create("principal-a", agent_id="knoa")
    artifacts = ArtifactStore(tmp_path / "attachments", db_path=database)
    registry = ToolRegistry()
    gateway = CapabilityGateway(
        registry,
        ToolStep(registry, ToolArgumentPolicy(tmp_path)),
        artifacts,
    )
    runtime = KnoaAgentRuntime(
        Provider(),
        ContextCheckpointRepository(tmp_path / "agent" / "context.db"),
        GatewayMCPConnector(gateway),
        system_prompt="system",
        health_probe=healthy,
    )
    execution = AgentExecutionService(
        AgentManager({"knoa": runtime}),
        AgentSessionBindingRepository(database),
        gateway,
        artifacts,
        capabilities_for=lambda _scope: frozenset(),
        resolver_for=resolver,
    )

    with pytest.raises(LookupError):
        async for _event in execution.execute_turn(
            ExecuteAgentTurn(
                scope=scope,
                turn_id="turn-a",
                client_request_id="request-a",
                input="hello",
                attachments=(),
                tools_enabled=False,
                cancellation=asyncio.Event(),
                agent_id="codex",
            )
        ):
            pass


@pytest.mark.asyncio
async def test_execution_service_binds_system_agent_with_system_lease(
    tmp_path: Path,
) -> None:
    database = tmp_path / "platform.db"
    sessions = RuntimeSessionRepository(database, handle_factory=lambda: "review-scope")
    scope = sessions.create("principal-a", activate=False, agent_id="reviewer_agent")
    artifacts = ArtifactStore(tmp_path / "attachments", db_path=database)
    registry = ToolRegistry()
    gateway = CapabilityGateway(
        registry,
        ToolStep(registry, ToolArgumentPolicy(tmp_path)),
        artifacts,
    )
    runtime = KnoaAgentRuntime(
        Provider(),
        ContextCheckpointRepository(
            tmp_path / "reviewer" / "context.db",
            session_id_factory=lambda: "reviewer-runtime-session",
        ),
        GatewayMCPConnector(gateway),
        system_prompt="restricted reviewer",
        health_probe=healthy,
        agent_id="reviewer_agent",
        display_name="Reviewer",
    )
    user_runtime = KnoaAgentRuntime(
        Provider(),
        ContextCheckpointRepository(tmp_path / "user" / "context.db"),
        GatewayMCPConnector(gateway),
        system_prompt="system",
        health_probe=healthy,
    )
    bindings = AgentSessionBindingRepository(database)
    execution = AgentExecutionService(
        AgentManager(
            {"knoa": user_runtime, "reviewer_agent": runtime},
            default_agent="knoa",
            enabled={"knoa": True, "reviewer_agent": True},
            system_agents=frozenset({"reviewer_agent"}),
        ),
        bindings,
        gateway,
        artifacts,
        capabilities_for=lambda _scope: frozenset(),
        resolver_for=resolver,
    )

    binding = await execution._ensure_binding(
        scope,
        "reviewer_agent",
        "reviewer-digest",
        invocation_kind="system",
    )

    assert binding.agent_id == "reviewer_agent"
    assert binding.runtime_session_ref == "reviewer-runtime-session"


@pytest.mark.asyncio
async def test_execution_service_serializes_turns_for_one_platform_session(
    tmp_path: Path,
) -> None:
    database = tmp_path / "platform.db"
    sessions = RuntimeSessionRepository(database, handle_factory=lambda: "session-a")
    scope = sessions.create("principal-a")
    artifacts = ArtifactStore(tmp_path / "attachments", db_path=database)
    registry = ToolRegistry()
    gateway = CapabilityGateway(
        registry,
        ToolStep(registry, ToolArgumentPolicy(tmp_path)),
        artifacts,
    )
    provider = SerialProvider()
    runtime = KnoaAgentRuntime(
        provider,
        ContextCheckpointRepository(tmp_path / "agent" / "context.db"),
        GatewayMCPConnector(gateway),
        system_prompt="system",
        health_probe=healthy,
    )
    execution = AgentExecutionService(
        AgentManager({"knoa": runtime}),
        AgentSessionBindingRepository(database),
        gateway,
        artifacts,
        capabilities_for=lambda _scope: frozenset(),
        resolver_for=resolver,
    )

    async def consume(turn_id: str) -> list:
        return [
            event
            async for event in execution.execute_turn(
                ExecuteAgentTurn(
                    scope=scope,
                    turn_id=turn_id,
                    client_request_id=f"request-{turn_id}",
                    input=turn_id,
                    attachments=(),
                    tools_enabled=False,
                    cancellation=asyncio.Event(),
                )
            )
        ]

    first = asyncio.create_task(consume("turn-a"))
    await provider.started.wait()
    second = asyncio.create_task(consume("turn-b"))
    await asyncio.sleep(0.05)

    assert provider.active == 1
    assert len(provider.requests) == 1
    provider.release.set()
    await asyncio.gather(first, second)

    assert provider.max_active == 1
    assert len(provider.requests) == 2


@pytest.mark.asyncio
async def test_bridge_cancellation_idle_watchdog_triggers_when_inactive():
    external = asyncio.Event()
    invocation = asyncio.Event()
    activity = asyncio.Event()
    reason_ref = [""]

    task = asyncio.create_task(
        AgentExecutionService._bridge_cancellation(
            external,
            invocation,
            deadline_seconds=10.0,
            activity_notifier=activity,
            reason_ref=reason_ref,
            idle_timeout_seconds=0.05,
        )
    )
    await invocation.wait()
    await task
    assert "idle timeout" in reason_ref[0]


@pytest.mark.asyncio
async def test_bridge_cancellation_idle_watchdog_resets_on_activity():
    external = asyncio.Event()
    invocation = asyncio.Event()
    activity = asyncio.Event()
    reason_ref = [""]

    task = asyncio.create_task(
        AgentExecutionService._bridge_cancellation(
            external,
            invocation,
            deadline_seconds=10.0,
            activity_notifier=activity,
            reason_ref=reason_ref,
            idle_timeout_seconds=0.1,
        )
    )
    # Simulate active streaming every 0.04s
    for _ in range(4):
        await asyncio.sleep(0.04)
        activity.set()
        assert not invocation.is_set()

    # Now stop activity and wait for idle timeout
    await invocation.wait()
    await task
    assert "idle timeout" in reason_ref[0]

