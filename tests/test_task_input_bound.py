"""Oversized turn inputs spill to artifacts instead of riding inline.

Regression coverage for local-model ``context_budget_exceeded`` failures:
a 20k-char GitLab failure snapshot used to ride inline in the trigger goal
and the task goal, exhausting 16k local windows before the first LLM call.
"""
from __future__ import annotations

import json
from pathlib import Path

from knoa_agent_contracts import TurnFinished
from knoa_platform.agent_runtime.session_store import RuntimeSessionRepository
from knoa_platform.artifacts import ArtifactStore
from knoa_platform.automation import (
    TriggerDispatcher,
    TriggerRepository,
    TriggerService,
)
from knoa_platform.tasks import (
    DurableApprovalService,
    DurableToolCommitService,
    TaskEventHub,
    TaskExecutor,
    TaskLaunchKind,
    TaskLaunchPolicy,
    TaskRepository,
    TaskService,
)
from knoa_platform.tasks.input_bound import (
    TURN_INPUT_SPILL_THRESHOLD_CHARS,
    bound_turn_input,
    summarize_trigger_payload,
)


def _store(tmp_path: Path) -> ArtifactStore:
    return ArtifactStore(
        root=tmp_path / "artifacts",
        db_path=tmp_path / "artifacts.db",
    )


# ------------------------------------------------------------------
# bound_turn_input unit behavior
# ------------------------------------------------------------------


def test_short_input_passes_through_unchanged(tmp_path: Path) -> None:
    text = "review merge request"
    assert bound_turn_input(text, artifacts=_store(tmp_path), session_id="s") == text


def test_overlong_input_spills_with_bounded_preview_and_pointer(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    text = "x" * (TURN_INPUT_SPILL_THRESHOLD_CHARS + 5000)
    bounded = bound_turn_input(text, artifacts=store, session_id="session-a")
    assert len(bounded) < len(text)
    assert len(bounded) <= 6000
    assert "omitted" in bounded
    assert "read_artifact" in bounded
    artifact_id = bounded.split("artifact '")[1].split("'")[0]
    recovered = store.read_text("session-a", artifact_id)
    assert recovered["content"] == text


def test_overlong_input_without_store_falls_back_to_truncation_note() -> None:
    text = "y" * (TURN_INPUT_SPILL_THRESHOLD_CHARS + 1000)
    bounded = bound_turn_input(text)
    assert len(bounded) < len(text)
    assert "truncated" in bounded


def test_overlong_input_survives_store_failure() -> None:
    class _Broken:
        def create_generated_text(self, *args, **kwargs):
            raise OSError("disk gone")

    text = "z" * (TURN_INPUT_SPILL_THRESHOLD_CHARS + 1000)
    bounded = bound_turn_input(text, artifacts=_Broken(), session_id="s")
    assert len(bounded) < len(text)


def test_summarize_trigger_payload_covers_mcp_snapshot() -> None:
    payload = {
        "server_id": "gitlab",
        "resource_uri": "gitlab://failed-pipelines/events/e1",
        "resource_name": "failed-pipeline",
        "contents": [
            {"uri": "u1", "mime_type": "text/markdown", "text": "trace tail " * 100},
        ],
    }
    summary = summarize_trigger_payload(payload)
    assert "gitlab" in summary
    assert "gitlab://failed-pipelines/events/e1" in summary
    assert "snapshot total" in summary
    assert len(summary) < 2000


def test_summarize_trigger_payload_covers_generic_events() -> None:
    summary = summarize_trigger_payload({"foo": "bar", "n": 1})
    assert "foo" in summary


# ------------------------------------------------------------------
# Scheme 1: trigger dispatcher spills oversized snapshots
# ------------------------------------------------------------------


def _trigger_components(tmp_path: Path, *, artifacts=None):
    database = tmp_path / "assistant.db"
    sessions = RuntimeSessionRepository(database)
    scope = sessions.create("principal-a")
    repository = TriggerRepository(database)
    tasks_calls: list = []

    class _Tasks:
        async def execute_bound_launch(self, principal_id, **kwargs):
            tasks_calls.append((principal_id, kwargs))
            from types import SimpleNamespace

            return SimpleNamespace(execution_id="execution-a")

    dispatcher = TriggerDispatcher(repository, _Tasks(), artifacts=artifacts)
    service = TriggerService(repository, dispatcher)
    return service, dispatcher, scope, tasks_calls


async def test_large_mcp_snapshot_goal_is_bounded_with_pointer(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    service, dispatcher, scope, calls = _trigger_components(
        tmp_path, artifacts=store
    )
    trigger = await service.create(
        scope,
        client_request_id="request-a",
        name="GitLab failure",
        goal="Analyze this failed pipeline.",
    )
    big_text = "failed job trace tail line\n" * 1200  # ~30k chars
    await service.receive(
        scope.principal_id,
        trigger.trigger_id,
        external_event_id="mcp-resource:event-big",
        payload={
            "server_id": "gitlab",
            "resource_uri": "gitlab://failed-pipelines/events/big",
            "resource_name": "failed-pipeline",
            "contents": [
                {
                    "uri": "gitlab://failed-pipelines/events/big",
                    "mime_type": "text/markdown",
                    "text": big_text,
                    "encoded_size": 0,
                }
            ],
        },
    )

    assert await dispatcher.dispatch_once() is True
    goal = calls[0][1]["goal_override"]
    assert len(goal) < TURN_INPUT_SPILL_THRESHOLD_CHARS
    assert goal.startswith("MCP server: gitlab\nMCP resource: ")
    assert "untrusted data" in goal
    assert "gitlab://failed-pipelines/events/big" in goal
    assert "read_artifact" in goal
    # Full snapshot recoverable from the artifact, not inline.
    assert big_text[:100] not in goal
    artifact_id = goal.split("artifact '")[1].split("'")[0]
    recovered = store.read_text(scope.session_handle, artifact_id)
    assert json.loads(recovered["content"])["server_id"] == "gitlab"


async def test_large_snapshot_without_store_falls_back_to_refresh_hint(
    tmp_path: Path,
) -> None:
    service, dispatcher, scope, calls = _trigger_components(tmp_path)
    trigger = await service.create(
        scope,
        client_request_id="request-a",
        name="GitLab failure",
        goal="Analyze this failed pipeline.",
    )
    await service.receive(
        scope.principal_id,
        trigger.trigger_id,
        external_event_id="mcp-resource:event-big",
        payload={"server_id": "gitlab", "blob": "t" * 20000},
    )

    assert await dispatcher.dispatch_once() is True
    goal = calls[0][1]["goal_override"]
    assert len(goal) < TURN_INPUT_SPILL_THRESHOLD_CHARS
    assert "MCP read tools" in goal


# ------------------------------------------------------------------
# Scheme 3: executor bounds overlong task goals at execution time
# ------------------------------------------------------------------


class _CapturingRuntime:
    def __init__(self) -> None:
        self.requests = []

    async def execute_turn(self, request):
        self.requests.append(request)
        yield TurnFinished(
            runtime_session_ref="agent-session-a",
            runtime_turn_ref=request.turn_id,
            occurred_at=1.0,
            status="completed",
            final_output="done",
        )

    async def health(self):
        from knoa_agent_contracts import RuntimeHealth

        return RuntimeHealth(healthy=True, state="ready")


async def test_executor_bounds_overlong_goal_but_keeps_stored_goal(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    database = tmp_path / "assistant.db"
    sessions = RuntimeSessionRepository(database)
    scope = sessions.create("principal-a")
    repository = TaskRepository(database)
    hub = TaskEventHub(subscriber_capacity=32)
    approvals = DurableApprovalService(repository, hub)
    commits = DurableToolCommitService(repository)
    runtime = _CapturingRuntime()
    executor = TaskExecutor(
        repository, sessions, runtime, approvals, commits, hub,
        artifacts=store,
    )
    service = TaskService(repository, executor, approvals, hub)
    await service.start()
    try:
        big_goal = "G" * (TURN_INPUT_SPILL_THRESHOLD_CHARS + 4000)
        _definition, execution = await service.create_definition(
            scope,
            client_request_id="definition-big",
            title="big goal",
            goal=big_goal,
            launch_policy=TaskLaunchPolicy(kind=TaskLaunchKind.IMMEDIATE),
        )
        assert execution is not None
        await runtime_entered(runtime)
    finally:
        await service.stop()

    turn_input = runtime.requests[0].input
    assert len(turn_input) < len(big_goal)
    assert "read_artifact" in turn_input
    # Stored execution keeps the full goal for display/audit.
    assert execution.goal_snapshot == big_goal


async def runtime_entered(runtime: _CapturingRuntime) -> None:
    import asyncio

    for _ in range(200):
        if runtime.requests:
            return
        await asyncio.sleep(0.05)
    raise AssertionError("executor never reached the runtime")
