"""Thin Core API v1 WebSocket client for durable Tasks."""
from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any, Protocol

import websockets

from pc_assistant.agent_runtime.contracts import (
    ArtifactDownloadResult,
    ArtifactTranscriptionResult,
    ConfigSetResult,
    HealthStatus,
    HistoryResult,
    MemoryClearResult,
    MemoryListResult,
    RuntimeStatus,
    ToolListResult,
)
from pc_assistant.artifacts import ArtifactRef
from pc_assistant.automation import ScheduleSpec, ScheduleState, TriggerState
from pc_assistant.service.core_api import (
    ApprovalResolvedMessage,
    AuthenticateRequest,
    AuthenticatedMessage,
    ArtifactDownloadedMessage,
    ArtifactInputRef,
    ArtifactTranscribedMessage,
    ArtifactUploadedMessage,
    CORE_WS_MAX_SIZE,
    CancelTaskRequest,
    ClearMemoryRequest,
    ConfigSetMessage,
    CoreError,
    CoreServerMessage,
    CreateScheduleRequest,
    CreateTriggerRequest,
    CreateSessionRequest,
    CreateTaskRequest,
    DownloadArtifactRequest,
    FireTriggerRequest,
    GetTaskRequest,
    GetHistoryRequest,
    GetStatusRequest,
    GetScheduleRequest,
    GetTriggerRequest,
    HealthMessage,
    HealthRequest,
    HistoryMessage,
    ListMemoryRequest,
    ListSchedulesRequest,
    ListTasksRequest,
    ListToolsRequest,
    ListTriggersRequest,
    MemoryClearedMessage,
    MemoryListMessage,
    PauseScheduleRequest,
    PauseTaskRequest,
    PauseTriggerRequest,
    PrincipalTaskEventMessage,
    PrincipalTaskEventsSubscribedMessage,
    ResolveApprovalRequest,
    ResumeScheduleRequest,
    ScheduleAcceptedMessage,
    ScheduleListMessage,
    ScheduleSnapshot,
    ScheduleSnapshotMessage,
    ResumeTaskRequest,
    ResumeTriggerRequest,
    SessionCreatedMessage,
    SetConfigRequest,
    StatusMessage,
    SubscribeTaskRequest,
    SubscribePrincipalTaskEventsRequest,
    TaskAcceptedMessage,
    TaskCancelResultMessage,
    TaskEventMessage,
    TaskListMessage,
    TaskPauseResultMessage,
    TaskResumedMessage,
    TaskSnapshot,
    TaskSnapshotMessage,
    TaskSubscribedMessage,
    TranscribeArtifactRequest,
    ToolsMessage,
    TriggerAcceptedMessage,
    TriggerEventAcceptedMessage,
    TriggerEventSnapshot,
    TriggerListMessage,
    TriggerSnapshot,
    TriggerSnapshotMessage,
    UploadArtifactRequest,
    parse_core_server_message_json,
)
from pc_assistant.tasks import PrincipalTaskEvent, TaskEvent, TaskOrigin, TaskState


class ClientWebSocket(Protocol):
    async def send(self, message: str) -> None: ...
    async def close(self) -> None: ...
    def __aiter__(self): ...


class CoreConnectionLostError(ConnectionError):
    pass


class CoreTaskBufferOverflowError(CoreConnectionLostError):
    pass


class CoreRequestTimeoutError(TimeoutError):
    pass


class CoreRequestError(RuntimeError):
    def __init__(self, error: CoreError) -> None:
        self.code = error.code
        self.correlation_id = error.correlation_id
        super().__init__(error.message)


ApprovalHandler = Callable[[TaskEvent], Awaitable[bool]]


class CoreClient:
    def __init__(
        self,
        websocket: ClientWebSocket,
        *,
        approval_handler: ApprovalHandler | None = None,
        request_timeout_seconds: float = 60.0,
        max_buffered_task_events: int = 256,
    ) -> None:
        if max_buffered_task_events < 1:
            raise ValueError("Task event buffer limit must be at least one")
        self._websocket = websocket
        self._approval_handler = approval_handler
        self._request_timeout = max(0.01, request_timeout_seconds)
        self._max_buffered_task_events = max_buffered_task_events
        self._reader_task: asyncio.Task[None] | None = None
        self._pending: dict[str, asyncio.Future[CoreServerMessage]] = {}
        self._subscription_queues: dict[
            str,
            asyncio.Queue[TaskEvent | PrincipalTaskEvent | Exception],
        ] = {}
        self._active_tasks: list[str] = []
        self._send_lock = asyncio.Lock()
        self._connected = False

    @classmethod
    async def connect(
        cls,
        uri: str,
        credential: str,
        *,
        approval_handler: ApprovalHandler | None = None,
        request_timeout_seconds: float = 60.0,
        max_buffered_task_events: int = 256,
    ) -> CoreClient:
        websocket = await websockets.connect(uri, max_size=CORE_WS_MAX_SIZE)
        client = cls(
            websocket,
            approval_handler=approval_handler,
            request_timeout_seconds=request_timeout_seconds,
            max_buffered_task_events=max_buffered_task_events,
        )
        await client.start(credential)
        return client

    @staticmethod
    def _request_id() -> str:
        return uuid.uuid4().hex

    async def start(self, credential: str) -> None:
        if self._reader_task is not None:
            raise RuntimeError("CoreClient is already started")
        self._connected = True
        self._reader_task = asyncio.create_task(self._reader_loop())
        try:
            response = await self._request(
                AuthenticateRequest(
                    request_id=self._request_id(),
                    credential=credential,
                )
            )
            if not isinstance(response, AuthenticatedMessage):
                raise RuntimeError(
                    "CoreServer returned an invalid authentication response"
                )
        except BaseException:
            await self.disconnect()
            raise

    async def disconnect(self) -> None:
        self._connected = False
        try:
            await self._websocket.close()
        finally:
            if self._reader_task is not None:
                self._reader_task.cancel()
                try:
                    await self._reader_task
                except (asyncio.CancelledError, Exception):
                    pass
                self._reader_task = None
            self._fail_all(CoreConnectionLostError("Core connection closed"))

    async def _send(self, request: Any) -> None:
        if not self._connected:
            raise CoreConnectionLostError("Core client is not connected")
        async with self._send_lock:
            await self._websocket.send(request.model_dump_json())

    async def _request(self, request: Any) -> CoreServerMessage:
        future: asyncio.Future[CoreServerMessage] = (
            asyncio.get_running_loop().create_future()
        )
        self._pending[request.request_id] = future
        try:
            try:
                await self._send(request)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                await self.disconnect()
                raise CoreConnectionLostError(
                    f"Core request send failed: {request.method}"
                ) from exc
            try:
                response = await asyncio.wait_for(
                    future,
                    timeout=self._request_timeout,
                )
            except asyncio.TimeoutError as exc:
                await self.disconnect()
                raise CoreRequestTimeoutError(
                    f"Core request timed out: {request.method}"
                ) from exc
        finally:
            self._pending.pop(request.request_id, None)
        if isinstance(response, CoreError):
            raise CoreRequestError(response)
        return response

    async def _reader_loop(self) -> None:
        failure: Exception = CoreConnectionLostError("Core connection lost")
        try:
            async for raw in self._websocket:
                message = parse_core_server_message_json(raw)
                if isinstance(message, TaskEventMessage):
                    queue = self._subscription_queues.get(message.request_id)
                    if queue is not None:
                        try:
                            queue.put_nowait(message.event)
                        except asyncio.QueueFull:
                            failure = CoreTaskBufferOverflowError(
                                "Core Task event buffer overflow"
                            )
                            await self._websocket.close()
                            break
                    continue
                if isinstance(message, PrincipalTaskEventMessage):
                    queue = self._subscription_queues.get(message.request_id)
                    if queue is not None:
                        try:
                            queue.put_nowait(message.feed_event)
                        except asyncio.QueueFull:
                            failure = CoreTaskBufferOverflowError(
                                "Core principal Task event buffer overflow"
                            )
                            await self._websocket.close()
                            break
                    continue
                if isinstance(
                    message,
                    (TaskSubscribedMessage, PrincipalTaskEventsSubscribedMessage),
                ):
                    future = self._pending.get(message.request_id)
                    if future is None or future.done():
                        failure = CoreConnectionLostError(
                            "Core protocol violation: unsolicited Task subscription"
                        )
                        await self._websocket.close()
                        break
                    self._subscription_queues.setdefault(
                        message.request_id,
                        asyncio.Queue(maxsize=self._max_buffered_task_events),
                    )
                if isinstance(message, CoreError):
                    queue = self._subscription_queues.get(message.request_id)
                    if queue is not None:
                        queue.put_nowait(CoreRequestError(message))
                        continue
                future = self._pending.get(message.request_id)
                if future is not None and not future.done():
                    future.set_result(message)
        except asyncio.CancelledError:
            return
        except Exception as exc:
            failure = CoreConnectionLostError(
                f"Core connection lost: {type(exc).__name__}"
            )
        finally:
            self._connected = False
            self._fail_all(failure)

    def _fail_all(self, error: Exception) -> None:
        for future in tuple(self._pending.values()):
            if not future.done():
                future.set_exception(error)
        for queue in tuple(self._subscription_queues.values()):
            while not queue.empty():
                queue.get_nowait()
            queue.put_nowait(error)

    async def create_session(self, *, activate: bool = True) -> str:
        response = await self._request(
            CreateSessionRequest(
                request_id=self._request_id(),
                activate=activate,
            )
        )
        if not isinstance(response, SessionCreatedMessage):
            raise RuntimeError("CoreServer returned an invalid session response")
        return response.session_handle

    async def health(self) -> HealthStatus:
        response = await self._request(HealthRequest(request_id=self._request_id()))
        if not isinstance(response, HealthMessage):
            raise RuntimeError("CoreServer returned an invalid health response")
        return response.result

    async def status(self, session_handle: str) -> RuntimeStatus:
        response = await self._request(
            GetStatusRequest(
                request_id=self._request_id(),
                session_handle=session_handle,
            )
        )
        if not isinstance(response, StatusMessage):
            raise RuntimeError("CoreServer returned an invalid status response")
        return response.result

    async def history(self, session_handle: str) -> HistoryResult:
        response = await self._request(
            GetHistoryRequest(
                request_id=self._request_id(),
                session_handle=session_handle,
            )
        )
        if not isinstance(response, HistoryMessage):
            raise RuntimeError("CoreServer returned an invalid history response")
        return response.result

    async def list_memory(self, session_handle: str) -> MemoryListResult:
        response = await self._request(
            ListMemoryRequest(
                request_id=self._request_id(),
                session_handle=session_handle,
            )
        )
        if not isinstance(response, MemoryListMessage):
            raise RuntimeError("CoreServer returned an invalid memory response")
        return response.result

    async def clear_memory(self, session_handle: str) -> MemoryClearResult:
        response = await self._request(
            ClearMemoryRequest(
                request_id=self._request_id(),
                session_handle=session_handle,
            )
        )
        if not isinstance(response, MemoryClearedMessage):
            raise RuntimeError("CoreServer returned an invalid memory clear response")
        return response.result

    async def list_tools(self, session_handle: str) -> ToolListResult:
        response = await self._request(
            ListToolsRequest(
                request_id=self._request_id(),
                session_handle=session_handle,
            )
        )
        if not isinstance(response, ToolsMessage):
            raise RuntimeError("CoreServer returned an invalid tools response")
        return response.result

    async def set_config(
        self,
        session_handle: str,
        field_name: str,
        value: bool | int | float | str,
    ) -> ConfigSetResult:
        response = await self._request(
            SetConfigRequest(
                request_id=self._request_id(),
                session_handle=session_handle,
                field_name=field_name,
                value=value,
            )
        )
        if not isinstance(response, ConfigSetMessage):
            raise RuntimeError("CoreServer returned an invalid config response")
        return response.result

    async def upload_artifact(
        self,
        session_handle: str,
        data_url: str,
        *,
        media_type: str = "image/jpeg",
        name: str = "",
        caption: str = "",
    ) -> ArtifactRef:
        response = await self._request(
            UploadArtifactRequest(
                request_id=self._request_id(),
                session_handle=session_handle,
                data_url=data_url,
                media_type=media_type,
                name=name,
                caption=caption,
            )
        )
        if not isinstance(response, ArtifactUploadedMessage):
            raise RuntimeError("CoreServer returned an invalid artifact response")
        return response.result

    async def download_artifact(
        self,
        session_handle: str,
        artifact_id: str,
    ) -> ArtifactDownloadResult:
        response = await self._request(
            DownloadArtifactRequest(
                request_id=self._request_id(),
                session_handle=session_handle,
                artifact_id=artifact_id,
            )
        )
        if not isinstance(response, ArtifactDownloadedMessage):
            raise RuntimeError(
                "CoreServer returned an invalid artifact download response"
            )
        return response.result

    async def transcribe_artifact(
        self,
        session_handle: str,
        artifact_id: str,
    ) -> ArtifactTranscriptionResult:
        response = await self._request(
            TranscribeArtifactRequest(
                request_id=self._request_id(),
                session_handle=session_handle,
                artifact_id=artifact_id,
            )
        )
        if not isinstance(response, ArtifactTranscribedMessage):
            raise RuntimeError(
                "CoreServer returned an invalid artifact transcription response"
            )
        return response.result

    async def create_task(
        self,
        session_handle: str,
        user_input: str = "",
        attachments: tuple[ArtifactInputRef, ...] = (),
        *,
        tools_enabled: bool = True,
        priority: int = 0,
        parent_task_id: str = "",
        origin: TaskOrigin = TaskOrigin.CHAT,
    ) -> TaskAcceptedMessage:
        response = await self._request(
            CreateTaskRequest(
                request_id=self._request_id(),
                session_handle=session_handle,
                input=user_input,
                attachments=attachments,
                tools_enabled=tools_enabled,
                priority=priority,
                parent_task_id=parent_task_id,
                origin=origin,
            )
        )
        if not isinstance(response, TaskAcceptedMessage):
            raise RuntimeError("CoreServer returned an invalid Task response")
        return response

    async def task_events(
        self,
        task_id: str,
        *,
        after_seq: int = 0,
    ) -> AsyncIterator[TaskEvent]:
        request_id = self._request_id()
        response = await self._request(
            SubscribeTaskRequest(
                request_id=request_id,
                task_id=task_id,
                after_seq=after_seq,
            )
        )
        if not isinstance(response, TaskSubscribedMessage):
            raise RuntimeError("CoreServer returned an invalid Task subscription")
        queue = self._subscription_queues[request_id]
        self._active_tasks.append(task_id)
        try:
            while True:
                item = await queue.get()
                if isinstance(item, Exception):
                    raise item
                if not isinstance(item, TaskEvent):
                    raise CoreConnectionLostError(
                        "Core protocol mixed Task subscription event types"
                    )
                if (
                    item.event_type == "approval_requested"
                    and self._approval_handler is not None
                ):
                    approved = False
                    try:
                        approved = bool(await self._approval_handler(item))
                    except asyncio.CancelledError:
                        raise
                    except Exception:
                        approved = False
                    await self.resolve_approval(
                        item.payload.approval_id,
                        approved=approved,
                    )
                yield item
                if item.event_type in {"completed", "failed", "cancelled"}:
                    return
        finally:
            self._subscription_queues.pop(request_id, None)
            if task_id in self._active_tasks:
                self._active_tasks.remove(task_id)

    async def principal_task_events(
        self,
        *,
        after_id: int = 0,
    ) -> AsyncIterator[PrincipalTaskEvent]:
        request_id = self._request_id()
        response = await self._request(
            SubscribePrincipalTaskEventsRequest(
                request_id=request_id,
                after_id=after_id,
            )
        )
        if not isinstance(response, PrincipalTaskEventsSubscribedMessage):
            raise RuntimeError(
                "CoreServer returned an invalid principal Task event subscription"
            )
        queue = self._subscription_queues[request_id]
        try:
            while True:
                item = await queue.get()
                if isinstance(item, Exception):
                    raise item
                if not isinstance(item, PrincipalTaskEvent):
                    raise CoreConnectionLostError(
                        "Core protocol mixed Task subscription event types"
                    )
                yield item
        finally:
            self._subscription_queues.pop(request_id, None)

    async def get_task(self, task_id: str) -> TaskSnapshot:
        response = await self._request(
            GetTaskRequest(
                request_id=self._request_id(),
                task_id=task_id,
            )
        )
        if not isinstance(response, TaskSnapshotMessage):
            raise RuntimeError("CoreServer returned an invalid Task snapshot")
        return response.task

    async def list_tasks(
        self,
        *,
        session_handle: str = "",
        state: TaskState | None = None,
        origins: tuple[TaskOrigin, ...] = (),
        limit: int = 50,
        cursor: str = "",
    ) -> TaskListMessage:
        response = await self._request(
            ListTasksRequest(
                request_id=self._request_id(),
                session_handle=session_handle,
                state=state,
                origins=origins,
                limit=limit,
                cursor=cursor,
            )
        )
        if not isinstance(response, TaskListMessage):
            raise RuntimeError("CoreServer returned an invalid Task list")
        return response

    async def execute_task(
        self,
        session_handle: str,
        user_input: str = "",
        attachments: tuple[ArtifactInputRef, ...] = (),
        *,
        tools_enabled: bool = True,
        priority: int = 0,
    ) -> AsyncIterator[TaskEvent]:
        accepted = await self.create_task(
            session_handle,
            user_input,
            attachments,
            tools_enabled=tools_enabled,
            priority=priority,
        )
        async for event in self.task_events(accepted.task_id):
            yield event

    async def cancel_task(
        self,
        task_id: str,
        *,
        reason: str = "",
    ) -> TaskCancelResultMessage:
        response = await self._request(
            CancelTaskRequest(
                request_id=self._request_id(),
                task_id=task_id,
                reason=reason,
            )
        )
        if not isinstance(response, TaskCancelResultMessage):
            raise RuntimeError("CoreServer returned an invalid Task cancel response")
        return response

    async def cancel_active_task(self) -> TaskCancelResultMessage | None:
        if not self._active_tasks:
            return None
        return await self.cancel_task(self._active_tasks[-1])

    async def pause_task(self, task_id: str, *, reason: str = "") -> TaskPauseResultMessage:
        response = await self._request(
            PauseTaskRequest(
                request_id=self._request_id(),
                task_id=task_id,
                reason=reason,
            )
        )
        if not isinstance(response, TaskPauseResultMessage):
            raise RuntimeError("CoreServer returned an invalid Task pause response")
        return response

    async def resume_task(
        self,
        task_id: str,
        *,
        reason: str = "",
        acknowledge_outcome_unknown: bool = False,
    ) -> TaskResumedMessage:
        response = await self._request(
            ResumeTaskRequest(
                request_id=self._request_id(),
                task_id=task_id,
                reason=reason,
                acknowledge_outcome_unknown=acknowledge_outcome_unknown,
            )
        )
        if not isinstance(response, TaskResumedMessage):
            raise RuntimeError("CoreServer returned an invalid Task resume response")
        return response

    async def resolve_approval(
        self,
        approval_id: str,
        *,
        approved: bool,
    ) -> ApprovalResolvedMessage:
        response = await self._request(
            ResolveApprovalRequest(
                request_id=self._request_id(),
                approval_id=approval_id,
                approved=approved,
            )
        )
        if not isinstance(response, ApprovalResolvedMessage):
            raise RuntimeError("CoreServer returned an invalid approval response")
        return response

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

    @property
    def is_connected(self) -> bool:
        return self._connected

    def set_approval_handler(self, handler: ApprovalHandler | None) -> None:
        self._approval_handler = handler
