"""Background dispatcher from durable occurrences into durable Tasks."""
from __future__ import annotations

import asyncio
import logging
from typing import Protocol

from pc_assistant.agent_runtime.contracts import RuntimeScope
from pc_assistant.automation.models import ScheduleRecord, ScheduleSpec, ScheduleState
from pc_assistant.automation.repository import ScheduleRepository
from pc_assistant.tasks.models import TaskOrigin, TaskRecord


logger = logging.getLogger(__name__)


class TaskCreationPort(Protocol):
    async def create(
        self,
        scope: RuntimeScope,
        *,
        client_request_id: str,
        goal: str,
        tools_enabled: bool = True,
        priority: int = 0,
        origin: TaskOrigin = TaskOrigin.SCHEDULED,
    ) -> TaskRecord: ...


class ScheduleDispatcher:
    """Claim occurrences before idempotently creating Tasks."""

    def __init__(
        self,
        repository: ScheduleRepository,
        tasks: TaskCreationPort,
        *,
        worker_id: str = "schedule-worker",
        lease_seconds: float = 60.0,
        poll_interval: float = 1.0,
    ) -> None:
        if not 0.1 <= poll_interval <= 60.0:
            raise ValueError("Schedule poll interval must be between 0.1 and 60 seconds")
        self._repository = repository
        self._tasks = tasks
        self._worker_id = worker_id
        self._lease_seconds = lease_seconds
        self._poll_interval = poll_interval
        self._wake = asyncio.Event()
        self._worker: asyncio.Task[None] | None = None

    @property
    def started(self) -> bool:
        return self._worker is not None

    async def start(self) -> None:
        if self._worker is not None:
            raise RuntimeError("ScheduleDispatcher is already started")
        self._worker = asyncio.create_task(self._worker_loop())
        self._wake.set()

    async def stop(self) -> None:
        worker, self._worker = self._worker, None
        if worker is None:
            return
        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)

    def wake(self) -> None:
        self._wake.set()

    async def dispatch_once(self) -> bool:
        occurrence = await asyncio.to_thread(
            self._repository.claim_due,
            self._worker_id,
            lease_seconds=self._lease_seconds,
        )
        if occurrence is None:
            return False
        try:
            schedule = await asyncio.to_thread(
                self._repository.get,
                occurrence.principal_id,
                occurrence.schedule_id,
            )
            task = await self._tasks.create(
                RuntimeScope(
                    principal_id=occurrence.principal_id,
                    session_handle=occurrence.session_handle,
                ),
                client_request_id=f"schedule:{occurrence.occurrence_id}",
                goal=schedule.goal,
                tools_enabled=schedule.tools_enabled,
                priority=schedule.priority,
                origin=TaskOrigin.SCHEDULED,
            )
            await asyncio.to_thread(
                self._repository.mark_task_created,
                occurrence.occurrence_id,
                task.task_id,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception(
                "Schedule occurrence delivery failed: %s",
                occurrence.occurrence_id,
            )
            try:
                await asyncio.to_thread(
                    self._repository.mark_delivery_failed,
                    occurrence.occurrence_id,
                    failure_code=type(exc).__name__,
                )
            except Exception:
                logger.exception(
                    "Schedule delivery failure checkpoint failed: %s",
                    occurrence.occurrence_id,
                )
        return True

    async def _worker_loop(self) -> None:
        while True:
            self._wake.clear()
            try:
                while await self.dispatch_once():
                    pass
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Schedule dispatcher iteration failed")
            try:
                await asyncio.wait_for(
                    self._wake.wait(),
                    timeout=self._poll_interval,
                )
            except TimeoutError:
                pass


class ScheduleService:
    """Principal-owned schedule commands plus dispatcher wake-up."""

    def __init__(
        self,
        repository: ScheduleRepository,
        dispatcher: ScheduleDispatcher,
    ) -> None:
        self._repository = repository
        self._dispatcher = dispatcher

    async def create(
        self,
        scope: RuntimeScope,
        *,
        client_request_id: str,
        goal: str,
        spec: ScheduleSpec,
        tools_enabled: bool = True,
        priority: int = 0,
    ) -> ScheduleRecord:
        schedule, created = await asyncio.to_thread(
            self._repository.create,
            scope,
            client_request_id=client_request_id,
            goal=goal,
            spec=spec,
            tools_enabled=tools_enabled,
            priority=priority,
        )
        if created:
            self._dispatcher.wake()
        return schedule

    async def get(self, principal_id: str, schedule_id: str) -> ScheduleRecord:
        return await asyncio.to_thread(
            self._repository.get,
            principal_id,
            schedule_id,
        )

    async def list(
        self,
        principal_id: str,
        *,
        state: ScheduleState | None = None,
        limit: int = 50,
    ) -> tuple[ScheduleRecord, ...]:
        return await asyncio.to_thread(
            self._repository.list,
            principal_id,
            state=state,
            limit=limit,
        )

    async def pause(self, principal_id: str, schedule_id: str) -> ScheduleRecord:
        return await asyncio.to_thread(
            self._repository.pause,
            principal_id,
            schedule_id,
        )

    async def resume(self, principal_id: str, schedule_id: str) -> ScheduleRecord:
        schedule = await asyncio.to_thread(
            self._repository.resume,
            principal_id,
            schedule_id,
        )
        if schedule.state is ScheduleState.ACTIVE:
            self._dispatcher.wake()
        return schedule
