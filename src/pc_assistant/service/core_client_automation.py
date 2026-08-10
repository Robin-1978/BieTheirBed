"""Schedule and trigger operations mixed into the transport-only Core client."""
from __future__ import annotations

from typing import Any

from pc_assistant.automation import ScheduleSpec, ScheduleState, TriggerState
from pc_assistant.service.core_api import (
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


class CoreAutomationClientMixin:
    async def create_schedule(
        self,
        session_handle: str,
        goal: str,
        spec: ScheduleSpec,
        *,
        tools_enabled: bool = True,
        priority: int = 0,
    ) -> ScheduleSnapshot:
        response = await self._request(
            CreateScheduleRequest(
                request_id=self._request_id(),
                session_handle=session_handle,
                goal=goal,
                spec=spec,
                tools_enabled=tools_enabled,
                priority=priority,
            )
        )
        if not isinstance(response, ScheduleAcceptedMessage):
            raise RuntimeError("CoreServer returned an invalid schedule response")
        return response.schedule

    async def get_schedule(self, schedule_id: str) -> ScheduleSnapshot:
        response = await self._request(
            GetScheduleRequest(
                request_id=self._request_id(),
                schedule_id=schedule_id,
            )
        )
        if not isinstance(response, ScheduleSnapshotMessage):
            raise RuntimeError("CoreServer returned an invalid schedule snapshot")
        return response.schedule

    async def list_schedules(
        self,
        *,
        state: ScheduleState | None = None,
        limit: int = 50,
    ) -> tuple[ScheduleSnapshot, ...]:
        response = await self._request(
            ListSchedulesRequest(
                request_id=self._request_id(),
                state=state,
                limit=limit,
            )
        )
        if not isinstance(response, ScheduleListMessage):
            raise RuntimeError("CoreServer returned an invalid schedule list")
        return response.schedules

    async def pause_schedule(self, schedule_id: str) -> ScheduleSnapshot:
        response = await self._request(
            PauseScheduleRequest(
                request_id=self._request_id(),
                schedule_id=schedule_id,
            )
        )
        if not isinstance(response, ScheduleSnapshotMessage):
            raise RuntimeError("CoreServer returned an invalid paused schedule")
        return response.schedule

    async def resume_schedule(self, schedule_id: str) -> ScheduleSnapshot:
        response = await self._request(
            ResumeScheduleRequest(
                request_id=self._request_id(),
                schedule_id=schedule_id,
            )
        )
        if not isinstance(response, ScheduleSnapshotMessage):
            raise RuntimeError("CoreServer returned an invalid resumed schedule")
        return response.schedule

    async def create_trigger(
        self,
        session_handle: str,
        name: str,
        goal: str,
        *,
        tools_enabled: bool = True,
        priority: int = 0,
    ) -> TriggerSnapshot:
        response = await self._request(
            CreateTriggerRequest(
                request_id=self._request_id(),
                session_handle=session_handle,
                name=name,
                goal=goal,
                tools_enabled=tools_enabled,
                priority=priority,
            )
        )
        if not isinstance(response, TriggerAcceptedMessage):
            raise RuntimeError("CoreServer returned an invalid trigger response")
        return response.trigger

    async def get_trigger(self, trigger_id: str) -> TriggerSnapshot:
        response = await self._request(
            GetTriggerRequest(
                request_id=self._request_id(),
                trigger_id=trigger_id,
            )
        )
        if not isinstance(response, TriggerSnapshotMessage):
            raise RuntimeError("CoreServer returned an invalid trigger snapshot")
        return response.trigger

    async def list_triggers(
        self,
        *,
        state: TriggerState | None = None,
        limit: int = 50,
    ) -> tuple[TriggerSnapshot, ...]:
        response = await self._request(
            ListTriggersRequest(
                request_id=self._request_id(),
                state=state,
                limit=limit,
            )
        )
        if not isinstance(response, TriggerListMessage):
            raise RuntimeError("CoreServer returned an invalid trigger list")
        return response.triggers

    async def pause_trigger(self, trigger_id: str) -> TriggerSnapshot:
        response = await self._request(
            PauseTriggerRequest(
                request_id=self._request_id(),
                trigger_id=trigger_id,
            )
        )
        if not isinstance(response, TriggerSnapshotMessage):
            raise RuntimeError("CoreServer returned an invalid paused trigger")
        return response.trigger

    async def resume_trigger(self, trigger_id: str) -> TriggerSnapshot:
        response = await self._request(
            ResumeTriggerRequest(
                request_id=self._request_id(),
                trigger_id=trigger_id,
            )
        )
        if not isinstance(response, TriggerSnapshotMessage):
            raise RuntimeError("CoreServer returned an invalid resumed trigger")
        return response.trigger

    async def fire_trigger(
        self,
        trigger_id: str,
        external_event_id: str,
        payload: dict[str, Any] | None = None,
    ) -> TriggerEventSnapshot:
        response = await self._request(
            FireTriggerRequest(
                request_id=self._request_id(),
                trigger_id=trigger_id,
                external_event_id=external_event_id,
                payload=payload or {},
            )
        )
        if not isinstance(response, TriggerEventAcceptedMessage):
            raise RuntimeError("CoreServer returned an invalid trigger event response")
        return response.event
