"""Core-owned durable approval service and ToolStep confirmation port."""
from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass

from pc_assistant.agent_runtime.contracts import RuntimeScope
from pc_assistant.agent_runtime.tool_step import ProposedToolCall
from pc_assistant.tasks.event_hub import TaskEventHub
from pc_assistant.tasks.models import TaskApprovalRecord, TaskState
from pc_assistant.tasks.repository import TaskRepository


@dataclass(frozen=True)
class _ApprovalWaiter:
    task_id: str
    future: asyncio.Future[bool]


class DurableApprovalService:
    """Persist approval before delivery and resolve it from any owned client."""

    def __init__(self, repository: TaskRepository, events: TaskEventHub) -> None:
        self._repository = repository
        self._events = events
        self._waiters: dict[str, _ApprovalWaiter] = {}
        self._lock = asyncio.Lock()

    @staticmethod
    def _tool_step_id(task_id: str, call: ProposedToolCall) -> str:
        canonical = json.dumps(
            call.arguments,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        digest = hashlib.sha256(
            f"{task_id}\0{call.call_id}\0{call.name}\0{canonical}".encode("utf-8")
        ).hexdigest()
        return digest[:32]

    async def confirm(
        self,
        scope: RuntimeScope,
        run_id: str,
        call: ProposedToolCall,
        reason: str,
    ) -> bool:
        approval, event, _created = await asyncio.to_thread(
            self._repository.request_approval,
            scope.principal_id,
            run_id,
            tool_step_id=self._tool_step_id(run_id, call),
            tool_call_id=call.call_id,
            tool_name=call.name,
            arguments=call.arguments,
            reason=reason,
        )
        if approval.state.value != "pending":
            return approval.state.value == "approved"
        future = asyncio.get_running_loop().create_future()
        async with self._lock:
            existing = self._waiters.get(approval.approval_id)
            if existing is not None:
                raise RuntimeError("Approval already has a live executor waiter")
            self._waiters[approval.approval_id] = _ApprovalWaiter(
                task_id=run_id,
                future=future,
            )
        await self._events.publish(event)
        try:
            return await future
        finally:
            async with self._lock:
                current = self._waiters.get(approval.approval_id)
                if current is not None and current.future is future:
                    self._waiters.pop(approval.approval_id, None)

    async def resolve(
        self,
        principal_id: str,
        approval_id: str,
        *,
        approved: bool,
        resolved_by: str = "",
    ) -> tuple[TaskApprovalRecord, bool, TaskState]:
        async with self._lock:
            waiter = self._waiters.get(approval_id)
            resume_state = (
                TaskState.RUNNING if waiter is not None else TaskState.QUEUED
            )
            approval, event, changed = await asyncio.to_thread(
                self._repository.resolve_approval,
                principal_id,
                approval_id,
                approved=approved,
                resume_state=resume_state,
                resolved_by=resolved_by,
            )
            if changed and waiter is not None and not waiter.future.done():
                waiter.future.set_result(approved)
        if event is not None:
            await self._events.publish(event)
        return approval, changed, resume_state

    async def cancel_task(self, task_id: str) -> None:
        async with self._lock:
            for approval_id, waiter in tuple(self._waiters.items()):
                if waiter.task_id != task_id:
                    continue
                self._waiters.pop(approval_id, None)
                if not waiter.future.done():
                    waiter.future.set_result(False)

    async def close(self) -> None:
        async with self._lock:
            waiters, self._waiters = self._waiters, {}
            for waiter in waiters.values():
                if not waiter.future.done():
                    waiter.future.cancel()
