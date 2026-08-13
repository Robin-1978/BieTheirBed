"""Connection-independent execution of persisted Tasks."""
from __future__ import annotations

import asyncio
import logging
import time
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
    HealthStatus,
    RuntimeScope,
)
from knoa_platform.agent_runtime.session_store import RuntimeSessionRepository
from knoa_platform.agent_runtime.tool_step import ToolOutcomeUnknownError
from knoa_platform.agents import AgentExecutionService, ExecuteAgentTurn
from knoa_platform.context.session_context import SessionContextService
from knoa_platform.tasks.approval import DurableApprovalService
from knoa_platform.tasks.event_hub import TaskEventHub
from knoa_platform.tasks.models import (
    TERMINAL_TASK_STATES,
    TaskRecord,
    TaskState,
    TaskTraceEntry,
)
from knoa_platform.tasks.repository import TaskRepository
from knoa_platform.tasks.tool_commit import DurableToolCommitService


logger = logging.getLogger(__name__)


class TaskExecutor:
    """Claim durable work and stream AgentRuntime output into the journal."""

    def __init__(
        self,
        repository: TaskRepository,
        sessions: RuntimeSessionRepository,
        agents: AgentExecutionService,
        approvals: DurableApprovalService,
        tool_commits: DurableToolCommitService,
        events: TaskEventHub,
        *,
        worker_id: str = "core-worker",
        lease_seconds: float = 60.0,
        max_concurrency: int = 4,
        session_context: SessionContextService | None = None,
    ) -> None:
        if not 1 <= max_concurrency <= 32:
            raise ValueError("Task concurrency must be between 1 and 32")
        self._repository = repository
        self._sessions = sessions
        self._agents = agents
        self._approvals = approvals
        self._tool_commits = tool_commits
        self._events = events
        self._worker_id = worker_id
        self._lease_seconds = lease_seconds
        self._max_concurrency = max_concurrency
        self._session_context = session_context
        self._wake = asyncio.Event()
        self._worker: asyncio.Task[None] | None = None
        self._executions: set[asyncio.Task[None]] = set()
        self._active: dict[str, asyncio.Event] = {}

    @property
    def started(self) -> bool:
        return self._worker is not None

    async def start(self) -> None:
        if self._worker is not None:
            raise RuntimeError("TaskExecutor is already started")
        recovered = await asyncio.to_thread(self._repository.recover_interrupted)
        for event in recovered:
            await self._events.publish(event)
        self._worker = asyncio.create_task(self._worker_loop())
        self._wake.set()

    async def stop(self) -> None:
        worker, self._worker = self._worker, None
        if worker is None:
            return
        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)
        executions, self._executions = self._executions, set()
        for execution in executions:
            execution.cancel()
        if executions:
            await asyncio.gather(*executions, return_exceptions=True)
        self._active.clear()

    def wake(self) -> None:
        self._wake.set()

    def signal_cancel(self, task_id: str) -> None:
        cancellation = self._active.get(task_id)
        if cancellation is not None:
            cancellation.set()

    def agent_id(self, scope: RuntimeScope) -> str:
        return self._sessions.agent_id(scope)

    async def health_check(self) -> HealthStatus:
        health = await self._agents.health()
        return HealthStatus(healthy=health.healthy, detail=health.detail)

    async def _worker_loop(self) -> None:
        while True:
            self._wake.clear()
            self._executions = {
                execution
                for execution in self._executions
                if not execution.done()
            }
            while len(self._executions) < self._max_concurrency:
                task = await asyncio.to_thread(
                    self._repository.claim_next,
                    self._worker_id,
                    lease_seconds=self._lease_seconds,
                )
                if task is None:
                    break
                execution = asyncio.create_task(self._execute(task))
                self._executions.add(execution)
                execution.add_done_callback(lambda _task: self._wake.set())
            await self._wake.wait()

    @staticmethod
    def _trace_entry(event: RuntimeTurnEvent) -> TaskTraceEntry | None:
        if isinstance(event, UsageReported):
            return None
        if isinstance(event, ReasoningSummaryDelta):
            entry_type, content = "reasoning", event.content
        elif isinstance(event, AssistantDelta):
            entry_type, content = "content", event.content
        elif isinstance(event, PlanChanged):
            entry_type, content = "plan", event.content
        elif isinstance(event, ToolCallStarted):
            entry_type, content = "tool_call", ""
        elif isinstance(event, ToolCallFinished):
            entry_type, content = "tool_result", ""
        elif isinstance(event, ContextCompacted):
            entry_type, content = "context_compacted", "Agent context compacted"
        elif isinstance(event, RuntimeWarning):
            entry_type, content = "warning", event.message
        elif isinstance(event, ArtifactProduced):
            return None
        elif isinstance(event, TurnFinished):
            entry_type, content = "final_output", event.final_output
        else:
            return None
        return TaskTraceEntry(
            entry_type=entry_type,
            content=content,
            tool_call_id=(
                event.tool_call_id
                if isinstance(event, (ToolCallStarted, ToolCallFinished))
                else ""
            ),
            tool_name=(
                event.tool_name
                if isinstance(event, (ToolCallStarted, ToolCallFinished))
                else ""
            ),
            tool_args=event.arguments if isinstance(event, ToolCallStarted) else {},
            tool_result=(
                {
                    "status": event.status,
                    "code": event.code,
                    "output": event.output,
                }
                if isinstance(event, ToolCallFinished)
                else None
            ),
            occurred_at=time.time(),
        )

    @staticmethod
    def _append_trace_entry(
        entries: list[TaskTraceEntry],
        entry: TaskTraceEntry,
    ) -> None:
        if (
            entry.entry_type in {"reasoning", "content"}
            and entries
            and entries[-1].entry_type == entry.entry_type
            and entries[-1].iteration == entry.iteration
        ):
            previous = entries[-1]
            entries[-1] = previous.model_copy(
                update={
                    "content": previous.content + entry.content,
                    "occurred_at": entry.occurred_at,
                }
            )
            return
        entries.append(entry)

    async def _save_trace(
        self,
        task: TaskRecord,
        entries: list[TaskTraceEntry],
        final_output: str,
    ) -> None:
        await asyncio.to_thread(
            self._repository.save_trace,
            task.principal_id,
            task.task_id,
            entries=tuple(entries),
            final_output=final_output,
        )

    async def _transition(
        self,
        task: TaskRecord,
        state: TaskState,
        **metadata: str,
    ) -> TaskRecord:
        updated, event = await asyncio.to_thread(
            self._repository.transition,
            task.principal_id,
            task.task_id,
            state,
            **metadata,
        )
        await self._events.publish(event)
        return updated

    async def _execute(self, task: TaskRecord) -> None:
        cancellation = asyncio.Event()
        self._active[task.task_id] = cancellation
        existing_trace = await asyncio.to_thread(
            self._repository.get_trace,
            task.principal_id,
            task.task_id,
        )
        entries = list(existing_trace.entries) if existing_trace is not None else []
        final_output = existing_trace.final_output if existing_trace is not None else ""
        trace_dirty = False
        last_trace_flush = time.monotonic()
        try:
            scope = RuntimeScope(
                principal_id=task.principal_id,
                session_handle=task.session_handle,
            )
            terminal: TurnFinished | None = None
            async for runtime_event in self._agents.execute_turn(
                ExecuteAgentTurn(
                    scope=scope,
                    turn_id=task.task_id,
                    client_request_id=task.client_request_id,
                    input=task.goal,
                    attachments=task.attachments,
                    tools_enabled=task.tools_enabled,
                    cancellation=cancellation,
                    agent_id=await asyncio.to_thread(
                        self._sessions.agent_id, scope
                    ),
                    confirmation=self._approvals,
                    tool_commit=self._tool_commits,
                )
            ):
                current = await asyncio.to_thread(
                    self._repository.get,
                    task.principal_id,
                    task.task_id,
                )
                if current.state in TERMINAL_TASK_STATES:
                    cancellation.set()
                    break
                if current.cancel_requested:
                    cancellation.set()
                if current.phase == "pause_requested":
                    cancellation.set()
                if cancellation.is_set():
                    break
                entry = self._trace_entry(runtime_event)
                if entry is not None:
                    self._append_trace_entry(entries, entry)
                    trace_dirty = True
                if isinstance(runtime_event, TurnFinished):
                    terminal = runtime_event
                    final_output = runtime_event.final_output
                now = time.monotonic()
                if (
                    not isinstance(
                        runtime_event,
                        (ReasoningSummaryDelta, AssistantDelta),
                    )
                    or now - last_trace_flush >= 0.5
                ):
                    await self._save_trace(task, entries, final_output)
                    trace_dirty = False
                    last_trace_flush = now

            current = await asyncio.to_thread(
                self._repository.get,
                task.principal_id,
                task.task_id,
            )
            if current.state in TERMINAL_TASK_STATES:
                return
            if current.state is TaskState.PAUSED:
                return
            if current.cancel_requested or cancellation.is_set():
                if current.phase == "pause_requested":
                    await self._transition(
                        task,
                        TaskState.PAUSED,
                        phase="manual_pause",
                        reason="Task pause reached a safe boundary",
                    )
                    return
                await self._transition(
                    task,
                    TaskState.CANCELLED,
                    reason="Task cancellation reached a safe boundary",
                )
                return
            if current.state is TaskState.WAITING_APPROVAL:
                return
            if terminal is None:
                raise RuntimeError("Agent execution ended without terminal event")
            if terminal.status == "outcome_unknown":
                raise ToolOutcomeUnknownError("Agent Turn outcome is unknown")
            if terminal.status not in {"completed", "interrupted"}:
                raise RuntimeError(terminal.error_code or terminal.status)
            await self._transition(
                task,
                TaskState.COMPLETED,
                final_summary=final_output or "Task completed",
            )
        except asyncio.CancelledError:
            cancellation.set()
            raise
        except ToolOutcomeUnknownError:
            logger.exception("Task tool outcome is unknown: %s", task.task_id)
            try:
                _, event = await asyncio.to_thread(
                    self._repository.pause_for_unknown_tool_outcome,
                    task.principal_id,
                    task.task_id,
                    reason=(
                        "A tool returned but its durable outcome checkpoint failed; "
                        "automatic replay is blocked"
                    ),
                )
                await self._events.publish(event)
            except Exception:
                logger.exception(
                    "Unknown tool outcome state could not be persisted: %s",
                    task.task_id,
                )
        except Exception as exc:
            logger.exception("Task execution failed: %s", task.task_id)
            try:
                current = await asyncio.to_thread(
                    self._repository.get,
                    task.principal_id,
                    task.task_id,
                )
                if current.state not in TERMINAL_TASK_STATES:
                    if current.cancel_requested:
                        await self._transition(
                            task,
                            TaskState.CANCELLED,
                            reason="Task cancelled after execution failure",
                        )
                    elif current.state is not TaskState.WAITING_APPROVAL:
                        await self._transition(
                            task,
                            TaskState.FAILED,
                            failure_code=type(exc).__name__,
                            reason="Task execution failed",
                        )
            except Exception:
                logger.exception("Task failure state could not be persisted")
        finally:
            if trace_dirty:
                try:
                    await self._save_trace(task, entries, final_output)
                except Exception:
                    logger.exception("Task execution trace could not be persisted")
            self._active.pop(task.task_id, None)
