"""Core-owned durable approval service and ToolStep confirmation port."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass

from knoa_platform.agent_runtime.contracts import RuntimeScope
from knoa_platform.agent_runtime.tool_step import ProposedToolCall
from knoa_platform.approvals import (
    ApprovalReviewDecision,
    ApprovalReviewer,
    ApprovalReviewMode,
    ApprovalReviewRequest,
)
from knoa_platform.tasks.event_hub import TaskEventHub
from knoa_platform.tasks.identity import task_approval_action_id
from knoa_platform.tasks.models import TaskApprovalRecord, TaskState
from knoa_platform.tasks.repository import TaskRepository


@dataclass(frozen=True)
class _ApprovalWaiter:
    task_id: str
    future: asyncio.Future[bool]


class DurableApprovalService:
    """Persist approval before delivery and resolve it from any owned client."""

    def __init__(
        self,
        repository: TaskRepository,
        events: TaskEventHub,
        *,
        reviewer: ApprovalReviewer | None = None,
        review_mode: ApprovalReviewMode = ApprovalReviewMode.OFF,
        auto_max_risk: str = "medium",
    ) -> None:
        self._repository = repository
        self._events = events
        self._waiters: dict[str, _ApprovalWaiter] = {}
        self._lock = asyncio.Lock()
        self._reviewer = reviewer
        self._review_mode = review_mode
        self._auto_max_risk = auto_max_risk

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
            tool_step_id=task_approval_action_id(run_id, call),
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
        try:
            if (
                _created
                and self._reviewer is not None
                and self._review_mode is not ApprovalReviewMode.OFF
            ):
                task = await asyncio.to_thread(
                    self._repository.get,
                    scope.principal_id,
                    run_id,
                )
                review = await self._reviewer.review(
                    ApprovalReviewRequest(
                        principal_id=scope.principal_id,
                        run_id=run_id,
                        human_instruction=task.goal,
                        proposed_action={
                            "tool_name": call.name,
                            "arguments": call.arguments,
                            "effect": reason.partition(":")[0] or "unknown",
                            "risk": reason.partition(":")[2] or "high",
                            "reason": reason,
                        },
                    )
                )
                rules = ",".join(review.rule_ids)
                approval, event = await asyncio.to_thread(
                    self._repository.annotate_approval_review,
                    scope.principal_id,
                    approval.approval_id,
                    reason=(
                        f"{reason}; reviewer[{review.reviewer_id}/{review.model}]="
                        f"{review.decision.value}: {review.reason}"
                        f"{'; rules=' + rules if rules else ''}"
                    )[:2000],
                )
            await self._events.publish(event)
            if (
                _created
                and self._reviewer is not None
                and self._review_mode is ApprovalReviewMode.AUTO
                and self._may_auto_resolve(review.decision, reason)
            ):
                resolved, _changed, _state = await self.resolve(
                    scope.principal_id,
                    approval.approval_id,
                    approved=review.decision is ApprovalReviewDecision.APPROVE,
                    resolved_by=f"approval_reviewer:{review.reviewer_id}",
                )
                return resolved.state.value == "approved"
            return await future
        finally:
            async with self._lock:
                current = self._waiters.get(approval.approval_id)
                if current is not None and current.future is future:
                    self._waiters.pop(approval.approval_id, None)

    def _may_auto_resolve(
        self,
        decision: ApprovalReviewDecision,
        policy_reason: str,
    ) -> bool:
        if self._review_mode is not ApprovalReviewMode.AUTO:
            return False
        if decision is ApprovalReviewDecision.ESCALATE:
            return False
        risk = policy_reason.partition(":")[2] or "high"
        allowed = {"low"} if self._auto_max_risk == "low" else {"low", "medium"}
        return risk in allowed

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
