"""Conversation application service invoking AgentRuntime without Task persistence."""
from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field

from knoa_agent_contracts import (
    ArtifactProduced,
    AssistantDelta,
    ContextCompacted,
    PlanChanged,
    ReasoningSummaryDelta,
    RuntimeTurnEvent,
    RuntimeWarning,
    ToolCallFinished,
    ToolCallStarted,
    TurnFinished,
    UsageReported,
)
from knoa_platform.agent_runtime.contracts import (
    ArtifactAttachment,
    RuntimeScope,
)
from knoa_platform.agent_runtime.session_store import (
    RuntimeSessionRepository,
)
from knoa_platform.agent_runtime.tool_step import ProposedToolCall, ToolStepResult
from knoa_platform.agents import AgentExecutionService, ExecuteAgentTurn
from knoa_platform.approvals import (
    ApprovalReviewDecision,
    ApprovalReviewer,
    ApprovalReviewMode,
    ApprovalReviewRequest,
    ApprovalReviewResult,
)

from knoa_platform.artifacts import ArtifactRef, artifact_refs_from_tool_output
from knoa_platform.conversation.models import (
    TERMINAL_CHAT_TURN_STATES,
    ChatApproval,
    ChatTimelineEntry,
    ChatTurn,
    ChatTurnSignal,
    ChatTurnState,
    ConversationSession,
    ConversationSessionState,
)
from knoa_platform.conversation.repository import (
    ChatTurnNotFoundError,
    ConversationRepository,
    ConversationSessionConflictError,
)
from knoa_platform.interactions import HumanInteractionService, ScopedInteractionPort
from knoa_platform.tasks.identity import task_tool_step_id
from knoa_platform.tools.base import ToolPolicy


logger = logging.getLogger(__name__)


@dataclass
class _LiveTurn:
    cancellation: asyncio.Event
    revision: int
    reasoning: str = ""
    content: str = ""
    final_output: str = ""
    timeline: list[ChatTimelineEntry] = field(default_factory=list)
    artifacts: list[ArtifactRef] = field(default_factory=list)
    notify_task: asyncio.Task[None] | None = None


class _LatestSubscription:
    def __init__(self) -> None:
        self.queue: asyncio.Queue[ChatTurnSignal] = asyncio.Queue(maxsize=1)

    def publish(self, signal: ChatTurnSignal) -> None:
        if self.queue.full():
            try:
                self.queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
        self.queue.put_nowait(signal)


class ConversationHub:
    """Coalescing live Turn snapshots; provider chunks are never replayed."""

    def __init__(self) -> None:
        self._subscriptions: dict[str, set[_LatestSubscription]] = {}
        self._lock = asyncio.Lock()

    async def subscribe(self, turn_id: str) -> _LatestSubscription:
        subscription = _LatestSubscription()
        async with self._lock:
            self._subscriptions.setdefault(turn_id, set()).add(subscription)
        return subscription

    async def unsubscribe(self, turn_id: str, subscription: _LatestSubscription) -> None:
        async with self._lock:
            subscriptions = self._subscriptions.get(turn_id)
            if subscriptions is None:
                return
            subscriptions.discard(subscription)
            if not subscriptions:
                self._subscriptions.pop(turn_id, None)

    async def publish(self, signal: ChatTurnSignal) -> None:
        async with self._lock:
            subscriptions = tuple(self._subscriptions.get(signal.turn.turn_id, ()))
        for subscription in subscriptions:
            subscription.publish(signal)


class ConversationApprovalService:
    def __init__(
        self,
        repository: ConversationRepository,
        notify: Callable[[str], Awaitable[None]],
        *,
        reviewer: ApprovalReviewer | None = None,
        review_mode: ApprovalReviewMode = ApprovalReviewMode.OFF,
        auto_max_risk: str = "medium",
    ) -> None:
        self._repository = repository
        self._notify = notify
        self._waiters: dict[str, asyncio.Future[bool]] = {}
        self._lock = asyncio.Lock()
        self._reviewer = reviewer
        self._review_mode = review_mode
        self._auto_max_risk = auto_max_risk

    def configure_review(
        self,
        reviewer: ApprovalReviewer | None,
        mode: ApprovalReviewMode,
        auto_max_risk: str,
    ) -> None:
        self._reviewer = reviewer
        self._review_mode = mode
        self._auto_max_risk = auto_max_risk

    async def confirm(
        self,
        scope: RuntimeScope,
        run_id: str,
        call: ProposedToolCall,
        reason: str,
    ) -> bool:
        approval, _created = await asyncio.to_thread(
            self._repository.request_approval,
            scope.principal_id,
            run_id,
            step_id=task_tool_step_id(run_id, call),
            call=call,
            reason=reason,
        )
        if approval.state != "pending":
            return approval.state == "approved"
        future = asyncio.get_running_loop().create_future()
        async with self._lock:
            if approval.approval_id in self._waiters:
                raise RuntimeError("Approval already has a live waiter")
            self._waiters[approval.approval_id] = future
        review: ApprovalReviewResult | None = None
        review_task: asyncio.Task[ApprovalReviewResult] | None = None
        try:
            current = await asyncio.to_thread(
                self._repository.get_approval,
                scope.principal_id,
                approval.approval_id,
            )
            if current.state != "pending" and not future.done():
                future.set_result(current.state == "approved")
            review_request: ApprovalReviewRequest | None = None
            if (
                _created
                and self._reviewer is not None
                and self._review_mode is not ApprovalReviewMode.OFF
                and not future.done()
            ):
                turn = await asyncio.to_thread(
                    self._repository.get,
                    scope.principal_id,
                    run_id,
                )
                review_request = ApprovalReviewRequest(
                    principal_id=scope.principal_id,
                    run_id=run_id,
                    human_instruction=turn.user_input,
                    proposed_action={
                        "tool_name": call.name,
                        "arguments": call.arguments,
                        "effect": reason.partition(":")[0] or "unknown",
                        "risk": reason.partition(":")[2] or "high",
                        "reason": reason,
                    },
                )
                review_task = asyncio.create_task(
                    self._reviewer.review(review_request)
                )
                done, _pending = await asyncio.wait(
                    {review_task, future},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if future in done:
                    review_task.cancel()
                    await asyncio.gather(review_task, return_exceptions=True)
                    return await future
                try:
                    review = await review_task
                    rules = ",".join(review.rule_ids)
                    approval = await asyncio.to_thread(
                        self._repository.annotate_approval_review,
                        scope.principal_id,
                        approval.approval_id,
                        reason=(
                            f"{reason}; reviewer[{review.reviewer_id}/{review.model}]="
                            f"{review.decision.value}: {review.reason}"
                            f"{'; rules=' + rules if rules else ''}"
                        )[:2000],
                    )
                except Exception as reviewer_exc:
                    logger.warning(
                        "Conversation approval reviewer failed for run %s: %s",
                        run_id,
                        reviewer_exc,
                    )
                    review = None
            await self._notify(run_id)
            if (
                _created
                and self._reviewer is not None
                and self._review_mode is ApprovalReviewMode.AUTO
                and review is not None
                and self._may_auto_resolve(
                    review.decision,
                    reason,
                    instruction_truncated=(
                        review_request.instruction_truncated
                        if review_request is not None
                        else False
                    ),
                )
            ):
                resolved, _changed = await self.resolve(
                    scope.principal_id,
                    approval.approval_id,
                    approved=review.decision is ApprovalReviewDecision.APPROVE,
                    resolved_by=f"approval_reviewer:{review.reviewer_id}",
                )
                return resolved.state == "approved"
            return await future
        finally:
            if review_task is not None and not review_task.done():
                review_task.cancel()
                await asyncio.gather(review_task, return_exceptions=True)
            async with self._lock:
                if self._waiters.get(approval.approval_id) is future:
                    self._waiters.pop(approval.approval_id, None)

    def _may_auto_resolve(
        self,
        decision: ApprovalReviewDecision,
        policy_reason: str,
        *,
        instruction_truncated: bool = False,
    ) -> bool:
        if instruction_truncated:
            return False
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
    ) -> tuple[ChatApproval, bool]:
        approval, changed, turn_id = await asyncio.to_thread(
            self._repository.resolve_approval,
            principal_id,
            approval_id,
            approved=approved,
            resolved_by=resolved_by,
        )
        async with self._lock:
            waiter = self._waiters.get(approval_id)
            if changed and waiter is not None and not waiter.done():
                waiter.set_result(approved)
        await self._notify(turn_id)
        return approval, changed

    async def cancel_turn(self, principal_id: str, turn_id: str) -> None:
        approval_ids = await asyncio.to_thread(
            self._repository.expire_pending_approvals,
            principal_id,
            turn_id,
            resolved_by="turn_cancelled",
        )
        async with self._lock:
            for approval_id in approval_ids:
                waiter = self._waiters.get(approval_id)
                if waiter is not None and not waiter.done():
                    waiter.set_result(False)

    async def close(self) -> None:
        async with self._lock:
            waiters, self._waiters = self._waiters, {}
        for waiter in waiters.values():
            if not waiter.done():
                waiter.cancel()


class ConversationToolCommitService:
    def __init__(self, repository: ConversationRepository) -> None:
        self._repository = repository

    async def begin(
        self,
        scope: RuntimeScope,
        turn_id: str,
        call: ProposedToolCall,
        policy: ToolPolicy,
    ) -> ToolStepResult | None:
        step, created = await asyncio.to_thread(
            self._repository.begin_tool_step,
            scope.principal_id,
            turn_id,
            step_id=task_tool_step_id(turn_id, call),
            call=call,
            policy=policy,
        )
        if created:
            return None
        if step.state in {"completed", "failed"}:
            return ToolStepResult.model_validate(step.result)
        return ToolStepResult(
            call_id=call.call_id,
            tool_name=call.name,
            status="not_executed",
            code="tool_outcome_unknown",
            message="Previous tool outcome is not safely replayable",
        )

    async def finish(
        self,
        scope: RuntimeScope,
        turn_id: str,
        call: ProposedToolCall,
        policy: ToolPolicy,
        result: ToolStepResult,
    ) -> None:
        del policy
        await asyncio.to_thread(
            self._repository.finish_tool_step,
            scope.principal_id,
            turn_id,
            task_tool_step_id(turn_id, call),
            result,
        )


class ConversationService:
    def __init__(
        self,
        sessions: RuntimeSessionRepository,
        repository: ConversationRepository,
        agents: AgentExecutionService,
        *,
        hub: ConversationHub | None = None,
        interactions: HumanInteractionService | None = None,
        approval_reviewer: ApprovalReviewer | None = None,
        approval_review_mode: ApprovalReviewMode = ApprovalReviewMode.OFF,
        approval_auto_max_risk: str = "medium",
    ) -> None:
        self._sessions = sessions
        self._repository = repository
        self._agents = agents
        self._hub = hub or ConversationHub()
        self._live: dict[str, _LiveTurn] = {}
        self._executions: dict[str, asyncio.Task[None]] = {}
        self._approvals = ConversationApprovalService(
            repository,
            self._notify,
            reviewer=approval_reviewer,
            review_mode=approval_review_mode,
            auto_max_risk=approval_auto_max_risk,
        )
        self._tool_commits = ConversationToolCommitService(repository)
        self._interactions = interactions
        self._interaction_port: ScopedInteractionPort | None = (
            None if interactions is None else interactions.for_owner("conversation_turn")
        )

    def configure_approval_review(
        self,
        reviewer: ApprovalReviewer | None,
        mode: ApprovalReviewMode,
        auto_max_risk: str,
    ) -> None:
        self._approvals.configure_review(reviewer, mode, auto_max_risk)

    async def start(self) -> None:
        await asyncio.to_thread(self._repository.recover_interrupted)

    async def stop(self) -> None:
        turn_ids = tuple(self._executions)
        executions, self._executions = tuple(self._executions.values()), {}
        for turn_id in turn_ids:
            live = self._live.get(turn_id)
            if live is not None:
                live.cancellation.set()
        for execution in executions:
            execution.cancel()
        if executions:
            await asyncio.gather(*executions, return_exceptions=True)
        self._live.clear()
        await self._approvals.close()

    async def compact_expired_details(self) -> int:
        return await asyncio.to_thread(self._repository.compact_expired_details)

    async def get_session(self, principal_id: str, session_handle: str) -> ConversationSession:
        return await asyncio.to_thread(self._repository.get_session, principal_id, session_handle)

    async def list_sessions(
        self,
        principal_id: str,
        *,
        include_archived: bool = False,
        limit: int = 100,
        cursor: str = "",
    ) -> tuple[tuple[ConversationSession, ...], str]:
        return await asyncio.to_thread(
            self._repository.list_sessions,
            principal_id,
            include_archived=include_archived,
            limit=limit,
            cursor=cursor,
        )

    async def update_session(
        self,
        principal_id: str,
        session_handle: str,
        *,
        title: str | None = None,
        state: ConversationSessionState | None = None,
        expected_revision: int | None = None,
    ) -> ConversationSession:
        session = await asyncio.to_thread(
            self._repository.update_session,
            principal_id,
            session_handle,
            title=title,
            state=state,
            expected_revision=expected_revision,
        )
        if state is ConversationSessionState.ACTIVE:
            await asyncio.to_thread(
                self._sessions.set_active,
                RuntimeScope(principal_id=principal_id, session_handle=session_handle),
            )
        return session

    async def delete_session(self, principal_id: str, session_handle: str) -> None:
        scope = await asyncio.to_thread(self._sessions.resolve, principal_id, session_handle)
        active_turns, _next_cursor = await asyncio.to_thread(
            self._repository.list_session,
            principal_id,
            session_handle,
            limit=500,
        )
        if any(turn.state not in TERMINAL_CHAT_TURN_STATES for turn in active_turns):
            raise ConversationSessionConflictError("Conversation has an active turn")
        await asyncio.to_thread(self._sessions.delete, scope)

    async def retry_turn(self, principal_id: str, turn_id: str, *, client_request_id: str) -> ChatTurn:
        previous = await self.get_turn(principal_id, turn_id)
        if previous.state not in {ChatTurnState.FAILED, ChatTurnState.CANCELLED}:
            raise ValueError("Only failed or cancelled ChatTurns can be retried")
        return await self.create_turn(
            RuntimeScope(principal_id=principal_id, session_handle=previous.session_handle),
            client_request_id=client_request_id,
            user_input=previous.user_input,
            attachments=previous.attachments,
            tools_enabled=previous.tools_enabled,
        )

    async def create_turn(
        self,
        scope: RuntimeScope,
        *,
        client_request_id: str,
        user_input: str,
        attachments: tuple[ArtifactAttachment, ...] = (),
        tools_enabled: bool = True,
        agent_id: str | None = None,
    ) -> ChatTurn:
        owned = await asyncio.to_thread(
            self._sessions.resolve,
            scope.principal_id,
            scope.session_handle,
        )
        selected_agent = await asyncio.to_thread(self._sessions.agent_id, owned)
        if agent_id is not None and agent_id != selected_agent:
            raise ValueError("ChatTurn Agent must match the Session Agent")
        turn, created = await asyncio.to_thread(
            self._repository.create,
            owned,
            client_request_id=client_request_id,
            user_input=user_input,
            attachments=attachments,
            tools_enabled=tools_enabled,
        )
        if created:
            self._live[turn.turn_id] = _LiveTurn(
                cancellation=asyncio.Event(),
                revision=turn.revision,
            )
            execution = asyncio.create_task(self._execute(turn))
            self._executions[turn.turn_id] = execution
            execution.add_done_callback(
                lambda _task, turn_id=turn.turn_id: self._executions.pop(turn_id, None)
            )
        await self._notify(turn.turn_id)
        return await self.get_turn(scope.principal_id, turn.turn_id)

    async def get_turn(self, principal_id: str, turn_id: str) -> ChatTurn:
        stored = await asyncio.to_thread(self._repository.get, principal_id, turn_id)
        if self._interactions is not None:
            stored = stored.model_copy(
                update={
                    "interactions": await self._interactions.list_owner(
                        principal_id,
                        "conversation_turn",
                        turn_id,
                    )
                }
            )
        live = self._live.get(turn_id)
        if live is None or stored.state in TERMINAL_CHAT_TURN_STATES:
            return stored
        live.revision = max(live.revision, stored.revision)
        return stored.model_copy(
            update={
                "reasoning": live.reasoning,
                "content": live.content,
                "final_output": live.final_output,
                "timeline": tuple(live.timeline),
                "artifacts": tuple(live.artifacts),
                "revision": live.revision,
            }
        )

    async def list_turns(
        self,
        principal_id: str,
        session_handle: str,
        *,
        limit: int = 100,
        cursor: str = "",
    ) -> tuple[tuple[ChatTurn, ...], str]:
        turns, next_cursor = await asyncio.to_thread(
            self._repository.list_session,
            principal_id,
            session_handle,
            limit=limit,
            cursor=cursor,
        )
        resolved = []
        for turn in turns:
            resolved.append(await self.get_turn(principal_id, turn.turn_id))
        return tuple(resolved), next_cursor

    async def updates(
        self,
        principal_id: str,
        turn_id: str,
    ) -> AsyncIterator[ChatTurnSignal]:
        subscription = await self._hub.subscribe(turn_id)
        try:
            current = await self.get_turn(principal_id, turn_id)
            yield ChatTurnSignal(turn=current, kind="snapshot")
            if current.state in TERMINAL_CHAT_TURN_STATES:
                return
            while True:
                signal = await subscription.queue.get()
                if signal.turn.principal_id != principal_id:
                    continue
                yield signal
                if signal.turn.state in TERMINAL_CHAT_TURN_STATES:
                    return
        finally:
            await self._hub.unsubscribe(turn_id, subscription)

    async def cancel(self, principal_id: str, turn_id: str) -> ChatTurn:
        turn = await asyncio.to_thread(
            self._repository.request_cancel,
            principal_id,
            turn_id,
        )
        live = self._live.get(turn_id)
        if live is not None:
            live.cancellation.set()
        await self._approvals.cancel_turn(principal_id, turn_id)
        await self._notify(turn_id)
        return turn

    async def resolve_approval(
        self,
        principal_id: str,
        approval_id: str,
        *,
        approved: bool,
        resolved_by: str = "",
    ) -> tuple[ChatApproval, bool]:
        return await self._approvals.resolve(
            principal_id,
            approval_id,
            approved=approved,
            resolved_by=resolved_by,
        )

    async def _notify(self, turn_id: str) -> None:
        try:
            stored = await asyncio.to_thread(self._repository.get_by_id, turn_id)
        except ChatTurnNotFoundError:
            return
        await self._hub.publish(
            ChatTurnSignal(
                turn=await self.get_turn(stored.principal_id, turn_id),
            )
        )

    def _schedule_notify(self, turn_id: str) -> None:
        live = self._live.get(turn_id)
        if live is None or (live.notify_task is not None and not live.notify_task.done()):
            return

        async def publish_later() -> None:
            await asyncio.sleep(0.05)
            await self._notify(turn_id)

        live.notify_task = asyncio.create_task(publish_later())

    async def _flush_notify(self, turn_id: str) -> None:
        live = self._live.get(turn_id)
        if live is not None and live.notify_task is not None:
            notify_task, live.notify_task = live.notify_task, None
            if not notify_task.done():
                notify_task.cancel()
            await asyncio.gather(notify_task, return_exceptions=True)
        await self._notify(turn_id)

    async def _execute(self, turn: ChatTurn) -> None:
        live = self._live[turn.turn_id]
        scope = RuntimeScope(
            principal_id=turn.principal_id,
            session_handle=turn.session_handle,
        )
        try:
            terminal: TurnFinished | None = None
            selected_agent = await asyncio.to_thread(self._sessions.agent_id, scope)
            for attempt in range(2):
                terminal = None
                retry_safe = True
                async for event in self._agents.execute_turn(
                    ExecuteAgentTurn(
                        scope=scope,
                        turn_id=turn.turn_id,
                        client_request_id=turn.client_request_id,
                        input=turn.user_input,
                        attachments=turn.attachments,
                        tools_enabled=turn.tools_enabled,
                        cancellation=live.cancellation,
                        operation_id=(
                            "" if attempt == 0 else f"{turn.turn_id}:recovery:{attempt}"
                        ),
                        agent_id=selected_agent,
                        confirmation=self._approvals,
                        tool_commit=self._tool_commits,
                        interaction=self._interaction_port,
                    ),
                ):
                    if isinstance(event, TurnFinished):
                        terminal = event
                        continue
                    if not isinstance(event, UsageReported):
                        retry_safe = False
                    await self._apply_event(turn, live, event)
                if terminal is None:
                    raise RuntimeError("Agent execution ended without terminal event")
                if (
                    attempt == 0
                    and retry_safe
                    and not live.cancellation.is_set()
                    and terminal.status == "failed"
                    and terminal.error_code in {"provider_failed", "runtime_failed"}
                ):
                    continue
                await self._apply_event(turn, live, terminal)
                break
            if terminal is None:
                raise RuntimeError("Agent execution ended without terminal event")
            if terminal.status == "interrupted" or live.cancellation.is_set():
                state = ChatTurnState.CANCELLED
                failure_code = terminal.error_code or "cancelled"
            elif terminal.status == "completed":
                state = ChatTurnState.COMPLETED
                failure_code = ""
            else:
                state = ChatTurnState.FAILED
                failure_code = terminal.error_code or terminal.status
            await asyncio.to_thread(
                self._repository.checkpoint,
                turn.principal_id,
                turn.turn_id,
                state=state,
                reasoning=live.reasoning,
                content=live.content,
                final_output=live.final_output or live.content,
                timeline=tuple(live.timeline),
                artifacts=tuple(live.artifacts),
                failure_code=failure_code,
                revision=live.revision + 1,
                finished=True,
            )
        except asyncio.CancelledError:
            live.cancellation.set()
            await asyncio.to_thread(
                self._repository.checkpoint,
                turn.principal_id,
                turn.turn_id,
                state=ChatTurnState.CANCELLED,
                reasoning=live.reasoning,
                content=live.content,
                final_output=live.final_output,
                timeline=tuple(live.timeline),
                artifacts=tuple(live.artifacts),
                failure_code="cancelled",
                cancel_requested=True,
                revision=live.revision + 1,
                finished=True,
            )
            raise
        except Exception:  # noqa: BLE001 - Runtime failure becomes durable Turn state
            logger.exception(
                "Conversation runtime failed turn=%s session=%s",
                turn.turn_id,
                turn.session_handle,
            )
            await asyncio.to_thread(
                self._repository.checkpoint,
                turn.principal_id,
                turn.turn_id,
                state=(
                    ChatTurnState.CANCELLED
                    if live.cancellation.is_set()
                    else ChatTurnState.FAILED
                ),
                reasoning=live.reasoning,
                content=live.content,
                final_output=live.final_output,
                timeline=tuple(live.timeline),
                artifacts=tuple(live.artifacts),
                failure_code=("cancelled" if live.cancellation.is_set() else "runtime_failed"),
                revision=live.revision + 1,
                finished=True,
            )
        finally:
            await self._flush_notify(turn.turn_id)
            self._live.pop(turn.turn_id, None)

    async def _apply_event(
        self,
        turn: ChatTurn,
        live: _LiveTurn,
        event: RuntimeTurnEvent,
    ) -> None:
        changed = True
        if isinstance(event, ReasoningSummaryDelta):
            live.reasoning += event.content
            self._append_timeline_text(live, "reasoning", event)
        elif isinstance(event, AssistantDelta):
            live.content += event.content
            self._append_timeline_text(live, "content", event)
        elif isinstance(event, TurnFinished):
            live.final_output = event.final_output
        elif isinstance(event, ToolCallStarted):
            live.timeline.append(
                ChatTimelineEntry(
                    kind="tool_call",
                    tool_call_id=event.tool_call_id,
                    tool_name=event.tool_name,
                    tool_args=event.arguments,
                )
            )
        elif isinstance(event, ToolCallFinished):
            live.timeline.append(
                ChatTimelineEntry(
                    kind="tool_result",
                    tool_call_id=event.tool_call_id,
                    tool_name=event.tool_name,
                    tool_result={
                        "status": event.status,
                        "code": event.code,
                        "output": event.output,
                    },
                    blocked=event.status != "completed",
                )
            )
            if event.status == "completed":
                known_artifact_ids = {
                    artifact.artifact_id for artifact in live.artifacts
                }
                for artifact in artifact_refs_from_tool_output(event.output):
                    if artifact.artifact_id not in known_artifact_ids:
                        live.artifacts.append(artifact)
                        known_artifact_ids.add(artifact.artifact_id)
        elif isinstance(event, (PlanChanged, RuntimeWarning, ContextCompacted)):
            live.timeline.append(
                ChatTimelineEntry(
                    kind="notice",
                    content=(
                        event.content
                        if isinstance(event, PlanChanged)
                        else event.message
                        if isinstance(event, RuntimeWarning)
                        else "Agent context compacted"
                    ),
                )
            )
        elif isinstance(event, (UsageReported, ArtifactProduced)):
            changed = False
        else:
            changed = False
        if changed:
            live.revision += 1
        self._schedule_notify(turn.turn_id)

    @staticmethod
    def _append_timeline_text(
        live: _LiveTurn,
        kind: str,
        event: AssistantDelta | ReasoningSummaryDelta,
    ) -> None:
        if not event.content:
            return
        for index in range(len(live.timeline) - 1, -1, -1):
            current = live.timeline[index]
            if current.kind not in {
                "reasoning",
                "content",
            }:
                break
            if current.kind == kind:
                live.timeline[index] = current.model_copy(
                    update={"content": current.content + event.content}
                )
                return
        live.timeline.append(
            ChatTimelineEntry(
                kind=kind,
                content=event.content,
            )
        )
