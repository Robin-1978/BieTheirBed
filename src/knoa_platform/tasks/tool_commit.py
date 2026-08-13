"""Durable ToolStep commit checkpoints owned by the Task domain."""
from __future__ import annotations

import asyncio

from knoa_platform.agent_runtime.contracts import RuntimeScope
from knoa_platform.agent_runtime.tool_step import ProposedToolCall, ToolStepResult
from knoa_platform.tasks.identity import task_tool_step_id
from knoa_platform.tasks.models import TaskToolStepState
from knoa_platform.tasks.repository import TaskRepository
from knoa_platform.tools.base import ToolPolicy


class DurableToolCommitService:
    """Deduplicate known outcomes and stop replay when commit outcome is unknown."""

    def __init__(self, repository: TaskRepository) -> None:
        self._repository = repository

    async def begin(
        self,
        scope: RuntimeScope,
        task_id: str,
        call: ProposedToolCall,
        policy: ToolPolicy,
    ) -> ToolStepResult | None:
        record, created = await asyncio.to_thread(
            self._repository.begin_tool_step,
            scope.principal_id,
            task_id,
            tool_step_id=task_tool_step_id(task_id, call),
            tool_call_id=call.call_id,
            tool_name=call.name,
            arguments=call.arguments,
            effect=policy.effect.value,
            risk=policy.risk.value,
        )
        if created:
            return None
        if record.state in {
            TaskToolStepState.COMPLETED,
            TaskToolStepState.FAILED,
        }:
            return ToolStepResult.model_validate(record.result)
        return ToolStepResult(
            call_id=call.call_id,
            tool_name=call.name,
            status="not_executed",
            code="tool_outcome_unknown",
            message="A previous execution may have committed; automatic replay is blocked",
        )

    async def finish(
        self,
        scope: RuntimeScope,
        task_id: str,
        call: ProposedToolCall,
        policy: ToolPolicy,
        result: ToolStepResult,
    ) -> None:
        del policy
        state = (
            TaskToolStepState.COMPLETED
            if result.status == "completed"
            else TaskToolStepState.FAILED
        )
        await asyncio.to_thread(
            self._repository.finish_tool_step,
            scope.principal_id,
            task_id,
            task_tool_step_id(task_id, call),
            state=state,
            result=result.model_dump(mode="json"),
        )
