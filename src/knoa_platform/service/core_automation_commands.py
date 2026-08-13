"""Schedule and trigger Core command handlers."""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from knoa_platform.agent_runtime.contracts import RuntimeScope
from knoa_platform.automation import ScheduleService, TriggerService
from knoa_platform.service.core_api import (
    CreateScheduleRequest,
    CreateTriggerRequest,
    FireTriggerRequest,
    GetScheduleRequest,
    GetTriggerRequest,
    ListSchedulesRequest,
    ListTriggersRequest,
    PauseScheduleRequest,
    PauseTriggerRequest,
    ResumeScheduleRequest,
    ResumeTriggerRequest,
    ScheduleAcceptedMessage,
    ScheduleListMessage,
    ScheduleSnapshot,
    ScheduleSnapshotMessage,
    TriggerAcceptedMessage,
    TriggerEventAcceptedMessage,
    TriggerEventSnapshot,
    TriggerListMessage,
    TriggerSnapshot,
    TriggerSnapshotMessage,
)

Send = Callable[[Any], Awaitable[None]]


class AutomationCommandHandler:
    def __init__(self, schedules: ScheduleService, triggers: TriggerService) -> None:
        self._schedules = schedules
        self._triggers = triggers

    async def dispatch(self, principal: str, request: Any, send: Send) -> bool:
        if isinstance(request, CreateScheduleRequest):
            schedule = await self._schedules.create(
                RuntimeScope(
                    principal_id=principal,
                    session_handle=request.session_handle,
                ),
                client_request_id=request.request_id,
                goal=request.goal,
                spec=request.spec,
                tools_enabled=request.tools_enabled,
                priority=request.priority,
            )
            await send(ScheduleAcceptedMessage(
                request_id=request.request_id,
                schedule=ScheduleSnapshot.from_record(schedule),
            ))
        elif isinstance(request, GetScheduleRequest):
            schedule = await self._schedules.get(principal, request.schedule_id)
            await send(ScheduleSnapshotMessage(
                request_id=request.request_id,
                schedule=ScheduleSnapshot.from_record(schedule),
            ))
        elif isinstance(request, ListSchedulesRequest):
            schedules = await self._schedules.list(
                principal,
                state=request.state,
                limit=request.limit,
            )
            await send(ScheduleListMessage(
                request_id=request.request_id,
                schedules=tuple(
                    ScheduleSnapshot.from_record(schedule)
                    for schedule in schedules
                ),
            ))
        elif isinstance(request, PauseScheduleRequest):
            schedule = await self._schedules.pause(principal, request.schedule_id)
            await send(ScheduleSnapshotMessage(
                request_id=request.request_id,
                schedule=ScheduleSnapshot.from_record(schedule),
            ))
        elif isinstance(request, ResumeScheduleRequest):
            schedule = await self._schedules.resume(principal, request.schedule_id)
            await send(ScheduleSnapshotMessage(
                request_id=request.request_id,
                schedule=ScheduleSnapshot.from_record(schedule),
            ))
        elif isinstance(request, CreateTriggerRequest):
            trigger = await self._triggers.create(
                RuntimeScope(
                    principal_id=principal,
                    session_handle=request.session_handle,
                ),
                client_request_id=request.request_id,
                name=request.name,
                goal=request.goal,
                tools_enabled=request.tools_enabled,
                priority=request.priority,
            )
            await send(TriggerAcceptedMessage(
                request_id=request.request_id,
                trigger=TriggerSnapshot.from_record(trigger),
            ))
        elif isinstance(request, GetTriggerRequest):
            trigger = await self._triggers.get(principal, request.trigger_id)
            await send(TriggerSnapshotMessage(
                request_id=request.request_id,
                trigger=TriggerSnapshot.from_record(trigger),
            ))
        elif isinstance(request, ListTriggersRequest):
            triggers = await self._triggers.list(
                principal,
                state=request.state,
                limit=request.limit,
            )
            await send(TriggerListMessage(
                request_id=request.request_id,
                triggers=tuple(
                    TriggerSnapshot.from_record(trigger)
                    for trigger in triggers
                ),
            ))
        elif isinstance(request, PauseTriggerRequest):
            trigger = await self._triggers.set_paused(
                principal,
                request.trigger_id,
                paused=True,
            )
            await send(TriggerSnapshotMessage(
                request_id=request.request_id,
                trigger=TriggerSnapshot.from_record(trigger),
            ))
        elif isinstance(request, ResumeTriggerRequest):
            trigger = await self._triggers.set_paused(
                principal,
                request.trigger_id,
                paused=False,
            )
            await send(TriggerSnapshotMessage(
                request_id=request.request_id,
                trigger=TriggerSnapshot.from_record(trigger),
            ))
        elif isinstance(request, FireTriggerRequest):
            event = await self._triggers.receive(
                principal,
                request.trigger_id,
                external_event_id=request.external_event_id,
                payload=request.payload,
            )
            await send(TriggerEventAcceptedMessage(
                request_id=request.request_id,
                event=TriggerEventSnapshot.from_record(event),
            ))
        else:
            return False
        return True
