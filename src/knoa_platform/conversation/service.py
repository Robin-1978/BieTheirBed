"""Conversation application service invoking AgentRuntime without Task persistence."""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

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
from knoa_platform.artifacts import ArtifactRef
from knoa_platform.agents import AgentExecutionService, ExecuteAgentTurn
from knoa_platform.conversation.models import (
    ChatApproval,
    ChatTurn,
    ChatTurnSignal,
    ChatTurnState,
    ConversationSession,
    ConversationSessionState,
    ChatTimelineEntry,
    TERMINAL_CHAT_TURN_STATES,
)
from knoa_platform.conversation.repository import (
    ChatTurnNotFoundError,
    ConversationSessionConflictError,
    ConversationRepository,
)
from knoa_platform.context.session_context import SessionContextService
from knoa_platform.tasks.identity import task_tool_step_id
from knoa_platform.tools.base import ToolPolicy


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


@dataclass
class _SessionLease:
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    users: int = 0


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
    ) -> None:
        self._repository = repository
        self._notify = notify
        self._waiters: dict[str, asyncio.Future[bool]] = {}
        self._lock = asyncio.Lock()

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
        await self._notify(run_id)
        try:
            return await future
        finally:
            async with self._lock:
                if self._waiters.get(approval.approval_id) is future:
                    self._waiters.pop(approval.approval_id, None)

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
        session_context: SessionContextService | None = None,
    ) -> None:
        self._sessions = sessions
        self._repository = repository
        self._agents = agents
        self._session_context = session_context
        self._hub = hub or ConversationHub()
        self._live: dict[str, _LiveTurn] = {}
        self._executions: dict[str, asyncio.Task[None]] = {}
        self._approvals = ConversationApprovalService(repository, self._notify)
        self._tool_commits = ConversationToolCommitService(repository)
        self._session_leases: dict[str, _SessionLease] = {}
        self._session_leases_guard = asyncio.Lock()

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
        live = self._live.get(turn_id)
        if live is not None:
            live.cancellation.set()
        turn = await asyncio.to_thread(
            self._repository.checkpoint,
            principal_id,
            turn_id,
            cancel_requested=True,
        )
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
        async with self._session_lease(turn.session_handle):
            await self._execute_locked(turn)

    async def _execute_locked(self, turn: ChatTurn) -> None:
        live = self._live[turn.turn_id]
        scope = RuntimeScope(
            principal_id=turn.principal_id,
            session_handle=turn.session_handle,
        )
        try:
            terminal: TurnFinished | None = None
            async for event in self._agents.execute_turn(
                ExecuteAgentTurn(
                    scope=scope,
                    turn_id=turn.turn_id,
                    client_request_id=turn.client_request_id,
                    input=turn.user_input,
                    attachments=turn.attachments,
                    tools_enabled=turn.tools_enabled,
                    cancellation=live.cancellation,
                    agent_id=await asyncio.to_thread(
                        self._sessions.agent_id, scope
                    ),
                    confirmation=self._approvals,
                    tool_commit=self._tool_commits,
                ),
            ):
                await self._apply_event(turn, live, event)
                if isinstance(event, TurnFinished):
                    terminal = event
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
        except Exception:
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

    @asynccontextmanager
    async def _session_lease(self, session_handle: str) -> AsyncIterator[None]:
        async with self._session_leases_guard:
            lease = self._session_leases.setdefault(session_handle, _SessionLease())
            lease.users += 1
        try:
            async with lease.lock:
                yield
        finally:
            async with self._session_leases_guard:
                lease.users -= 1
                if lease.users == 0:
                    self._session_leases.pop(session_handle, None)

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
