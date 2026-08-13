from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from knoa_platform.agent_runtime.session_store import RuntimeSessionRepository
from knoa_platform.agent_runtime.tool_step import (
    ProposedToolCall,
    ToolArgumentPolicy,
    ToolStep,
    ToolStepContext,
)
from knoa_platform.tasks import (
    DurableToolCommitService,
    TaskRepository,
    TaskState,
    TaskToolStepState,
    TaskTransitionError,
)
from knoa_platform.tools.base import (
    ToolBase,
    ToolCapability,
    ToolEffect,
    ToolPolicy,
    ToolRisk,
)
from knoa_platform.tools.registry import ToolRegistry


class _WriteTool(ToolBase):
    name = "write_once"
    effect = ToolEffect.LOCAL_WRITE
    capabilities = frozenset({ToolCapability.HOST_WRITE})
    risk = ToolRisk.MEDIUM

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def execute(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        return {"written": kwargs["value"]}

    def definition(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "inputSchema": {
                "type": "object",
                "properties": {"value": {"type": "string"}},
                "required": ["value"],
            },
        }


class _Approval:
    async def confirm(self, scope, run_id, call, reason: str) -> bool:
        del scope, run_id, call, reason
        return True


def _running_task(tmp_path: Path):
    database = tmp_path / "assistant.db"
    sessions = RuntimeSessionRepository(
        database,
        handle_factory=lambda: "session-a",
    )
    scope = sessions.create("principal-a")
    repository = TaskRepository(
        database,
        task_id_factory=lambda: "task-a",
    )
    task, _ = repository.create(
        scope,
        client_request_id="request-a",
        goal="write once",
    )
    repository.claim_next("worker-a")
    return repository, scope, task


@pytest.mark.asyncio
async def test_durable_tool_commit_reuses_known_result_without_reexecution(
    tmp_path: Path,
) -> None:
    repository, scope, task = _running_task(tmp_path)
    tool = _WriteTool()
    registry = ToolRegistry()
    registry.register(tool)
    step = ToolStep(registry, ToolArgumentPolicy(tmp_path))
    context = ToolStepContext(
        scope=scope,
        run_id=task.task_id,
        client_request_id=task.client_request_id,
        capabilities=frozenset({ToolCapability.HOST_WRITE}),
        cancellation=asyncio.Event(),
        confirmation=_Approval(),
        commit=DurableToolCommitService(repository),
    )
    call = ProposedToolCall(
        call_id="call-a",
        name=tool.name,
        arguments={"value": "hello"},
    )

    first = await step.execute(context, call)
    repeated = await step.execute(context, call)

    assert first.status == "completed"
    assert repeated == first
    assert tool.calls == [{"value": "hello"}]
    records = repository.list_tool_steps(scope.principal_id, task.task_id)
    assert len(records) == 1
    assert records[0].state is TaskToolStepState.COMPLETED
    assert records[0].result["output"] == {"written": "hello"}


@pytest.mark.asyncio
async def test_recovery_blocks_replay_of_unknown_tool_outcome(tmp_path: Path) -> None:
    repository, scope, task = _running_task(tmp_path)
    call = ProposedToolCall(
        call_id="call-a",
        name="write_once",
        arguments={"value": "hello"},
    )
    commits = DurableToolCommitService(repository)
    policy = ToolPolicy(
        effect=ToolEffect.LOCAL_WRITE,
        capabilities=frozenset({ToolCapability.HOST_WRITE}),
        risk=ToolRisk.MEDIUM,
    )
    assert await commits.begin(scope, task.task_id, call, policy) is None

    recovered = repository.recover_interrupted()
    paused = repository.get(scope.principal_id, task.task_id)
    attempts = repository.list_attempts(scope.principal_id, task.task_id)
    steps = repository.list_tool_steps(scope.principal_id, task.task_id)

    assert len(recovered) == 1
    assert paused.state is TaskState.PAUSED
    assert paused.phase == "outcome_unknown"
    assert attempts[0].state.value == "interrupted"
    assert steps[0].state is TaskToolStepState.OUTCOME_UNKNOWN

    with pytest.raises(TaskTransitionError, match="acknowledged"):
        repository.resume(scope.principal_id, task.task_id)
    repository.resume(
        scope.principal_id,
        task.task_id,
        acknowledge_outcome_unknown=True,
    )
    repository.claim_next("worker-b")
    blocked = await commits.begin(scope, task.task_id, call, policy)
    assert blocked is not None
    assert blocked.status == "not_executed"
    assert blocked.code == "tool_outcome_unknown"


def test_unknown_tool_outcome_is_quarantined_without_restart(tmp_path: Path) -> None:
    repository, scope, task = _running_task(tmp_path)
    repository.begin_tool_step(
        scope.principal_id,
        task.task_id,
        tool_step_id="step-a",
        tool_call_id="call-a",
        tool_name="write_once",
        arguments={"value": "hello"},
        effect="local_write",
        risk="medium",
    )

    paused, event = repository.pause_for_unknown_tool_outcome(
        scope.principal_id,
        task.task_id,
        reason="Tool returned but its terminal checkpoint failed",
    )

    assert paused.state is TaskState.PAUSED
    assert paused.phase == "outcome_unknown"
    assert event.payload.previous_state is TaskState.RUNNING
    assert event.payload.state is TaskState.PAUSED
    assert repository.list_attempts(
        scope.principal_id,
        task.task_id,
    )[0].state.value == "interrupted"
    assert repository.list_attempts(
        scope.principal_id,
        task.task_id,
    )[0].failure_code == "tool_outcome_unknown"
    assert repository.list_tool_steps(
        scope.principal_id,
        task.task_id,
    )[0].state is TaskToolStepState.OUTCOME_UNKNOWN
