"""Connection-independent execution of persisted Tasks."""
from __future__ import annotations

import asyncio
import logging
from typing import cast

from pc_assistant.agent_runtime.contracts import (
    AgentRuntimePort,
    HealthStatus,
    RunRequest,
    RuntimeEvent,
    RuntimeRunContext,
    RuntimeScope,
)
from pc_assistant.agent_runtime.tool_step import ToolOutcomeUnknownError
from pc_assistant.tasks.approval import DurableApprovalService
from pc_assistant.tasks.event_hub import TaskEventHub
from pc_assistant.tasks.models import (
    TERMINAL_TASK_STATES,
    TaskEvent,
    TaskEventPayload,
    TaskEventType,
    TaskRecord,
    TaskState,
)
from pc_assistant.tasks.repository import TaskRepository
from pc_assistant.tasks.tool_commit import DurableToolCommitService


logger = logging.getLogger(__name__)


class TaskExecutor:
    """Claim durable work and stream AgentRuntime output into the journal."""

    def __init__(
        self,
        repository: TaskRepository,
        runtime: AgentRuntimePort,
        approvals: DurableApprovalService,
        tool_commits: DurableToolCommitService,
        events: TaskEventHub,
        *,
        worker_id: str = "core-worker",
        lease_seconds: float = 60.0,
        max_concurrency: int = 4,
    ) -> None:
        if not 1 <= max_concurrency <= 32:
            raise ValueError("Task concurrency must be between 1 and 32")
        self._repository = repository
        self._runtime = runtime
        self._approvals = approvals
        self._tool_commits = tool_commits
        self._events = events
        self._worker_id = worker_id
        self._lease_seconds = lease_seconds
        self._max_concurrency = max_concurrency
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

    async def health_check(self) -> HealthStatus:
        return await self._runtime.health_check()

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
    def _payload(event: RuntimeEvent) -> TaskEventPayload:
        payload = event.payload
        return TaskEventPayload(
            content=payload.content,
            tool_call_id=payload.tool_call_id,
            tool_name=payload.tool_name,
            tool_args=payload.tool_args,
            tool_result=payload.tool_result,
            artifact=payload.artifact,
            artifact_id=(
                payload.artifact.artifact_id if payload.artifact is not None else ""
            ),
            blocked=payload.blocked,
            iteration=payload.iteration,
        )

    async def _persist_runtime_event(
        self,
        task: TaskRecord,
        runtime_event: RuntimeEvent,
    ) -> TaskEvent:
        event = await asyncio.to_thread(
            self._repository.append_event,
            task.principal_id,
            task.task_id,
            cast(TaskEventType, runtime_event.event_type),
            self._payload(runtime_event),
        )
        await self._events.publish(event)
        return event

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
        final_output = ""
        try:
            request = RunRequest(
                client_request_id=task.client_request_id,
                input=task.goal,
                attachments=task.attachments,
                tools_enabled=task.tools_enabled,
            )
            context = RuntimeRunContext(
                scope=RuntimeScope(
                    principal_id=task.principal_id,
                    session_handle=task.session_handle,
                ),
                run_id=task.task_id,
                cancellation=cancellation,
                confirmation=self._approvals,
                tool_commit=self._tool_commits,
            )
            async for runtime_event in self._runtime.run(context, request):
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
                await self._persist_runtime_event(task, runtime_event)
                if runtime_event.event_type == "final_output":
                    final_output = runtime_event.payload.content

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
            self._active.pop(task.task_id, None)
