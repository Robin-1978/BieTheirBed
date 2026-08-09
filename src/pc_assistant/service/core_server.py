"""Core API v1 WebSocket connection handler."""
from __future__ import annotations

import asyncio
import hmac
import uuid
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from pydantic import ValidationError

from pc_assistant.agent_runtime.artifact_service import (
    ArtifactDownloadTooLargeError,
    ArtifactNotFoundError,
    InvalidArtifactError,
)
from pc_assistant.agent_runtime.contracts import (
    ArtifactAttachment,
    ArtifactDownloadRequest,
    ArtifactServicePort,
    ArtifactUploadRequest,
    ConfigSetRequest,
    ControlServicePort,
    RuntimeScope,
)
from pc_assistant.automation import ScheduleService, TriggerService
from pc_assistant.automation.repository import (
    ScheduleIdempotencyConflictError,
    ScheduleNotFoundError,
    ScheduleTransitionError,
)
from pc_assistant.automation.trigger_repository import (
    TriggerIdempotencyConflictError,
    TriggerNotFoundError,
    TriggerTransitionError,
)
from pc_assistant.exceptions import SessionNotFoundError
from pc_assistant.service.core_api import (
    ApprovalResolvedMessage,
    AuthenticateRequest,
    AuthenticatedMessage,
    ArtifactDownloadedMessage,
    ArtifactUploadedMessage,
    CancelTaskRequest,
    ClearMemoryRequest,
    ConfigSetMessage,
    CoreError,
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
    ResumeTaskRequest,
    ResumeTriggerRequest,
    SessionCreatedMessage,
    ScheduleAcceptedMessage,
    ScheduleListMessage,
    ScheduleSnapshot,
    ScheduleSnapshotMessage,
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
    ToolsMessage,
    TriggerAcceptedMessage,
    TriggerEventAcceptedMessage,
    TriggerEventSnapshot,
    TriggerListMessage,
    TriggerSnapshot,
    TriggerSnapshotMessage,
    UploadArtifactRequest,
    parse_core_request_json,
)
from pc_assistant.service.credentials import verify_principal_credential
from pc_assistant.tasks import (
    TaskCapacityError,
    TaskIdempotencyConflictError,
    TaskNotFoundError,
    TaskService,
    TaskTransitionError,
)


class WebSocketConnection(Protocol):
    async def recv(self) -> str | bytes: ...
    async def send(self, message: str) -> None: ...
    def __aiter__(self): ...


class PrincipalAuthenticator(Protocol):
    async def authenticate(self, credential: str) -> str | None: ...


class StaticTokenAuthenticator:
    """Resolve configured credentials to principals using constant-time checks."""

    def __init__(self, credentials: dict[str, str]) -> None:
        if not credentials:
            raise ValueError("At least one TCP credential is required")
        normalized: list[tuple[str, str]] = []
        for credential, principal in credentials.items():
            if not credential.strip() or not principal.strip():
                raise ValueError("TCP credentials and principals must not be empty")
            normalized.append((credential, principal.strip()))
        self._credentials = tuple(normalized)

    async def authenticate(self, credential: str) -> str | None:
        for configured, principal in self._credentials:
            if hmac.compare_digest(credential, configured):
                return principal
        return None


class SignedPrincipalAuthenticator:
    """Authenticate short-lived principals issued by trusted local adapters."""

    def __init__(self, signing_key: str) -> None:
        if not signing_key.strip():
            raise ValueError("Signed principal authentication requires a key")
        self._signing_key = signing_key

    async def authenticate(self, credential: str) -> str | None:
        return verify_principal_credential(self._signing_key, credential)


class CompositeAuthenticator:
    """Try bounded authentication strategies in declared order."""

    def __init__(self, *authenticators: PrincipalAuthenticator) -> None:
        if not authenticators:
            raise ValueError("At least one authenticator is required")
        self._authenticators = authenticators

    async def authenticate(self, credential: str) -> str | None:
        for authenticator in self._authenticators:
            principal = await authenticator.authenticate(credential)
            if principal is not None:
                return principal
        return None


class CoreServer:
    """Expose Task commands and disposable Task-event subscriptions."""

    def __init__(
        self,
        tasks: TaskService,
        schedules: ScheduleService,
        triggers: TriggerService,
        control: ControlServicePort,
        artifacts: ArtifactServicePort,
        authenticator: PrincipalAuthenticator,
        *,
        authentication_timeout_seconds: float = 10.0,
        max_subscriptions_per_connection: int = 8,
    ) -> None:
        if max_subscriptions_per_connection < 1:
            raise ValueError("Task subscription limit must be at least one")
        self._tasks = tasks
        self._schedules = schedules
        self._triggers = triggers
        self._control = control
        self._artifacts = artifacts
        self._authenticator = authenticator
        self._authentication_timeout = max(0.01, authentication_timeout_seconds)
        self._max_subscriptions = max_subscriptions_per_connection

    @staticmethod
    def _error(request_id: str, code: str, message: str) -> CoreError:
        return CoreError(
            request_id=request_id or "unknown",
            code=code,
            message=message,
            correlation_id=uuid.uuid4().hex,
        )

    async def handle(self, websocket: WebSocketConnection) -> None:
        send_lock = asyncio.Lock()
        subscriptions: dict[str, asyncio.Task[None]] = {}

        async def send(message: Any) -> None:
            async with send_lock:
                await websocket.send(message.model_dump_json())

        try:
            try:
                first_raw = await asyncio.wait_for(
                    websocket.recv(),
                    timeout=self._authentication_timeout,
                )
                first = parse_core_request_json(first_raw)
            except asyncio.TimeoutError:
                return
            except (ValidationError, ValueError, TypeError):
                await send(
                    self._error(
                        "unknown",
                        "invalid_request",
                        "Invalid authentication request",
                    )
                )
                return
            if not isinstance(first, AuthenticateRequest):
                await send(
                    self._error(
                        first.request_id,
                        "unauthenticated",
                        "Authentication required",
                    )
                )
                return
            principal = await self._authenticator.authenticate(first.credential)
            if principal is None:
                await send(
                    self._error(
                        first.request_id,
                        "unauthenticated",
                        "Authentication failed",
                    )
                )
                return
            await send(AuthenticatedMessage(request_id=first.request_id))

            async for raw in websocket:
                try:
                    request = parse_core_request_json(raw)
                except (ValidationError, ValueError, TypeError):
                    await send(
                        self._error("unknown", "invalid_request", "Invalid request")
                    )
                    continue
                if isinstance(request, AuthenticateRequest):
                    await send(
                        self._error(
                            request.request_id,
                            "invalid_request",
                            "Already authenticated",
                        )
                    )
                    continue
                if isinstance(
                    request,
                    (SubscribeTaskRequest, SubscribePrincipalTaskEventsRequest),
                ):
                    if request.request_id in subscriptions:
                        await send(
                            self._error(
                                request.request_id,
                                "invalid_request",
                                "Duplicate request ID",
                            )
                        )
                        continue
                    if len(subscriptions) >= self._max_subscriptions:
                        await send(
                            self._error(
                                request.request_id,
                                "resource_exhausted",
                                "Task subscription limit reached",
                            )
                        )
                        continue
                    subscription = asyncio.create_task(
                        self._stream_task(principal, request, send)
                        if isinstance(request, SubscribeTaskRequest)
                        else self._stream_principal_tasks(principal, request, send)
                    )
                    subscriptions[request.request_id] = subscription
                    subscription.add_done_callback(
                        lambda _task, request_id=request.request_id: (
                            subscriptions.pop(request_id, None)
                        )
                    )
                    continue
                await self._dispatch_scalar(principal, request, send)
        finally:
            for subscription in tuple(subscriptions.values()):
                if not subscription.done():
                    subscription.cancel()
            if subscriptions:
                await asyncio.gather(
                    *subscriptions.values(),
                    return_exceptions=True,
                )

    async def _stream_task(
        self,
        principal: str,
        request: SubscribeTaskRequest,
        send: Callable[[Any], Awaitable[None]],
    ) -> None:
        try:
            await self._tasks.get(principal, request.task_id)
            await send(
                TaskSubscribedMessage(
                    request_id=request.request_id,
                    task_id=request.task_id,
                    after_seq=request.after_seq,
                )
            )
            async for event in self._tasks.events(
                principal,
                request.task_id,
                after_seq=request.after_seq,
            ):
                await send(
                    TaskEventMessage(
                        request_id=request.request_id,
                        event=event,
                    )
                )
        except TaskNotFoundError:
            await send(
                self._error(
                    request.request_id,
                    "task_not_found",
                    "Task not found",
                )
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            await send(
                self._error(
                    request.request_id,
                    "internal_error",
                    "Task subscription failed",
                )
            )

    async def _stream_principal_tasks(
        self,
        principal: str,
        request: SubscribePrincipalTaskEventsRequest,
        send: Callable[[Any], Awaitable[None]],
    ) -> None:
        try:
            await send(
                PrincipalTaskEventsSubscribedMessage(
                    request_id=request.request_id,
                    after_id=request.after_id,
                )
            )
            async for feed_event in self._tasks.principal_events(
                principal,
                after_id=request.after_id,
            ):
                await send(
                    PrincipalTaskEventMessage(
                        request_id=request.request_id,
                        feed_event=feed_event,
                    )
                )
        except asyncio.CancelledError:
            raise
        except Exception:
            await send(
                self._error(
                    request.request_id,
                    "internal_error",
                    "Principal Task event subscription failed",
                )
            )

    async def _dispatch_scalar(
        self,
        principal: str,
        request: Any,
        send: Callable[[Any], Awaitable[None]],
    ) -> None:
        try:
            if isinstance(request, CreateSessionRequest):
                scope = await self._control.create_session(principal)
                await send(
                    SessionCreatedMessage(
                        request_id=request.request_id,
                        session_handle=scope.session_handle,
                    )
                )
            elif isinstance(request, CreateTaskRequest):
                scope = RuntimeScope(
                    principal_id=principal,
                    session_handle=request.session_handle,
                )
                task = await self._tasks.create(
                    scope,
                    client_request_id=request.request_id,
                    goal=request.input,
                    attachments=tuple(
                        ArtifactAttachment(
                            artifact_id=item.artifact_id,
                            caption=item.caption,
                        )
                        for item in request.attachments
                    ),
                    tools_enabled=request.tools_enabled,
                    priority=request.priority,
                    parent_task_id=request.parent_task_id,
                )
                await send(
                    TaskAcceptedMessage(
                        request_id=request.request_id,
                        task_id=task.task_id,
                        state=task.state,
                    )
                )
            elif isinstance(request, GetTaskRequest):
                task = await self._tasks.get(principal, request.task_id)
                await send(
                    TaskSnapshotMessage(
                        request_id=request.request_id,
                        task=TaskSnapshot.from_record(task),
                    )
                )
            elif isinstance(request, ListTasksRequest):
                tasks, next_cursor = await self._tasks.list(
                    principal,
                    session_handle=request.session_handle,
                    state=request.state,
                    limit=request.limit,
                    cursor=request.cursor,
                )
                await send(
                    TaskListMessage(
                        request_id=request.request_id,
                        tasks=tuple(TaskSnapshot.from_record(task) for task in tasks),
                        next_cursor=next_cursor,
                    )
                )
            elif isinstance(request, CreateScheduleRequest):
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
                await send(
                    ScheduleAcceptedMessage(
                        request_id=request.request_id,
                        schedule=ScheduleSnapshot.from_record(schedule),
                    )
                )
            elif isinstance(request, GetScheduleRequest):
                schedule = await self._schedules.get(
                    principal,
                    request.schedule_id,
                )
                await send(
                    ScheduleSnapshotMessage(
                        request_id=request.request_id,
                        schedule=ScheduleSnapshot.from_record(schedule),
                    )
                )
            elif isinstance(request, ListSchedulesRequest):
                schedules = await self._schedules.list(
                    principal,
                    state=request.state,
                    limit=request.limit,
                )
                await send(
                    ScheduleListMessage(
                        request_id=request.request_id,
                        schedules=tuple(
                            ScheduleSnapshot.from_record(schedule)
                            for schedule in schedules
                        ),
                    )
                )
            elif isinstance(request, PauseScheduleRequest):
                schedule = await self._schedules.pause(
                    principal,
                    request.schedule_id,
                )
                await send(
                    ScheduleSnapshotMessage(
                        request_id=request.request_id,
                        schedule=ScheduleSnapshot.from_record(schedule),
                    )
                )
            elif isinstance(request, ResumeScheduleRequest):
                schedule = await self._schedules.resume(
                    principal,
                    request.schedule_id,
                )
                await send(
                    ScheduleSnapshotMessage(
                        request_id=request.request_id,
                        schedule=ScheduleSnapshot.from_record(schedule),
                    )
                )
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
                await send(
                    TriggerAcceptedMessage(
                        request_id=request.request_id,
                        trigger=TriggerSnapshot.from_record(trigger),
                    )
                )
            elif isinstance(request, GetTriggerRequest):
                trigger = await self._triggers.get(principal, request.trigger_id)
                await send(
                    TriggerSnapshotMessage(
                        request_id=request.request_id,
                        trigger=TriggerSnapshot.from_record(trigger),
                    )
                )
            elif isinstance(request, ListTriggersRequest):
                triggers = await self._triggers.list(
                    principal,
                    state=request.state,
                    limit=request.limit,
                )
                await send(
                    TriggerListMessage(
                        request_id=request.request_id,
                        triggers=tuple(
                            TriggerSnapshot.from_record(trigger)
                            for trigger in triggers
                        ),
                    )
                )
            elif isinstance(request, PauseTriggerRequest):
                trigger = await self._triggers.set_paused(
                    principal,
                    request.trigger_id,
                    paused=True,
                )
                await send(
                    TriggerSnapshotMessage(
                        request_id=request.request_id,
                        trigger=TriggerSnapshot.from_record(trigger),
                    )
                )
            elif isinstance(request, ResumeTriggerRequest):
                trigger = await self._triggers.set_paused(
                    principal,
                    request.trigger_id,
                    paused=False,
                )
                await send(
                    TriggerSnapshotMessage(
                        request_id=request.request_id,
                        trigger=TriggerSnapshot.from_record(trigger),
                    )
                )
            elif isinstance(request, FireTriggerRequest):
                event = await self._triggers.receive(
                    principal,
                    request.trigger_id,
                    external_event_id=request.external_event_id,
                    payload=request.payload,
                )
                await send(
                    TriggerEventAcceptedMessage(
                        request_id=request.request_id,
                        event=TriggerEventSnapshot.from_record(event),
                    )
                )
            elif isinstance(request, HealthRequest):
                await send(
                    HealthMessage(
                        request_id=request.request_id,
                        result=await self._tasks.health_check(),
                    )
                )
            elif isinstance(request, GetStatusRequest):
                scope = RuntimeScope(
                    principal_id=principal,
                    session_handle=request.session_handle,
                )
                await send(
                    StatusMessage(
                        request_id=request.request_id,
                        result=await self._control.get_status(scope),
                    )
                )
            elif isinstance(request, GetHistoryRequest):
                scope = RuntimeScope(
                    principal_id=principal,
                    session_handle=request.session_handle,
                )
                await send(
                    HistoryMessage(
                        request_id=request.request_id,
                        result=await self._control.get_history(scope),
                    )
                )
            elif isinstance(request, ListMemoryRequest):
                scope = RuntimeScope(
                    principal_id=principal,
                    session_handle=request.session_handle,
                )
                await send(
                    MemoryListMessage(
                        request_id=request.request_id,
                        result=await self._control.list_memory(scope),
                    )
                )
            elif isinstance(request, ClearMemoryRequest):
                scope = RuntimeScope(
                    principal_id=principal,
                    session_handle=request.session_handle,
                )
                await send(
                    MemoryClearedMessage(
                        request_id=request.request_id,
                        result=await self._control.clear_memory(scope),
                    )
                )
            elif isinstance(request, ListToolsRequest):
                scope = RuntimeScope(
                    principal_id=principal,
                    session_handle=request.session_handle,
                )
                await send(
                    ToolsMessage(
                        request_id=request.request_id,
                        result=await self._control.list_tools(scope),
                    )
                )
            elif isinstance(request, SetConfigRequest):
                scope = RuntimeScope(
                    principal_id=principal,
                    session_handle=request.session_handle,
                )
                result = await self._control.set_config(
                    scope,
                    ConfigSetRequest(
                        field_name=request.field_name,
                        value=request.value,
                    ),
                )
                await send(
                    ConfigSetMessage(request_id=request.request_id, result=result)
                )
            elif isinstance(request, CancelTaskRequest):
                result = await self._tasks.cancel(
                    principal,
                    request.task_id,
                    reason=request.reason,
                )
                await send(
                    TaskCancelResultMessage(
                        request_id=request.request_id,
                        result=result,
                    )
                )
            elif isinstance(request, PauseTaskRequest):
                result = await self._tasks.pause(
                    principal,
                    request.task_id,
                    reason=request.reason,
                )
                await send(
                    TaskPauseResultMessage(
                        request_id=request.request_id,
                        result=result,
                    )
                )
            elif isinstance(request, ResumeTaskRequest):
                task = await self._tasks.resume(
                    principal,
                    request.task_id,
                    reason=request.reason,
                    acknowledge_outcome_unknown=(
                        request.acknowledge_outcome_unknown
                    ),
                )
                await send(
                    TaskResumedMessage(
                        request_id=request.request_id,
                        task_id=task.task_id,
                        state=task.state,
                    )
                )
            elif isinstance(request, ResolveApprovalRequest):
                approval, changed = await self._tasks.resolve_approval(
                    principal,
                    request.approval_id,
                    approved=request.approved,
                    resolved_by="core_api",
                )
                await send(
                    ApprovalResolvedMessage(
                        request_id=request.request_id,
                        approval_id=approval.approval_id,
                        resolved=changed,
                        state=approval.state,
                    )
                )
            elif isinstance(request, UploadArtifactRequest):
                scope = RuntimeScope(
                    principal_id=principal,
                    session_handle=request.session_handle,
                )
                result = await self._artifacts.upload(
                    scope,
                    ArtifactUploadRequest(
                        data_url=request.data_url,
                        media_type=request.media_type,
                        caption=request.caption,
                    ),
                )
                await send(
                    ArtifactUploadedMessage(
                        request_id=request.request_id,
                        result=result,
                    )
                )
            elif isinstance(request, DownloadArtifactRequest):
                scope = RuntimeScope(
                    principal_id=principal,
                    session_handle=request.session_handle,
                )
                result = await self._artifacts.download(
                    scope,
                    ArtifactDownloadRequest(artifact_id=request.artifact_id),
                )
                delivered = result.model_copy(
                    update={
                        "artifact": result.artifact.model_copy(
                            update={"status": "delivered"}
                        )
                    }
                )
                await send(
                    ArtifactDownloadedMessage(
                        request_id=request.request_id,
                        result=delivered,
                    )
                )
                try:
                    await self._artifacts.acknowledge_delivery(
                        scope,
                        request.artifact_id,
                    )
                except Exception:
                    pass
            else:
                await send(
                    self._error(
                        request.request_id,
                        "invalid_request",
                        "Unsupported request",
                    )
                )
        except SessionNotFoundError:
            await send(
                self._error(
                    request.request_id,
                    "session_not_found",
                    "Session not found",
                )
            )
        except TaskNotFoundError:
            await send(
                self._error(
                    request.request_id,
                    "task_not_found",
                    "Task not found",
                )
            )
        except ScheduleNotFoundError:
            await send(
                self._error(
                    request.request_id,
                    "schedule_not_found",
                    "Schedule not found",
                )
            )
        except ScheduleIdempotencyConflictError:
            await send(
                self._error(
                    request.request_id,
                    "invalid_request",
                    "Schedule request ID conflicts with an existing schedule",
                )
            )
        except ScheduleTransitionError:
            await send(
                self._error(
                    request.request_id,
                    "invalid_request",
                    "Schedule state does not allow this command",
                )
            )
        except TriggerNotFoundError:
            await send(
                self._error(
                    request.request_id,
                    "trigger_not_found",
                    "Trigger not found",
                )
            )
        except TriggerIdempotencyConflictError:
            await send(
                self._error(
                    request.request_id,
                    "invalid_request",
                    "Trigger or external event ID conflicts with existing input",
                )
            )
        except TriggerTransitionError:
            await send(
                self._error(
                    request.request_id,
                    "invalid_request",
                    "Trigger state does not allow this command",
                )
            )
        except TaskIdempotencyConflictError:
            await send(
                self._error(
                    request.request_id,
                    "invalid_request",
                    "Task request ID conflicts with an existing Task",
                )
            )
        except TaskCapacityError:
            await send(
                self._error(
                    request.request_id,
                    "resource_exhausted",
                    "Active Task limit reached",
                )
            )
        except TaskTransitionError:
            await send(
                self._error(
                    request.request_id,
                    "invalid_request",
                    "Task state does not allow this command",
                )
            )
        except PermissionError:
            await send(
                self._error(
                    request.request_id,
                    "capability_denied",
                    "Capability denied",
                )
            )
        except InvalidArtifactError:
            await send(
                self._error(
                    request.request_id,
                    "invalid_request",
                    "Invalid artifact",
                )
            )
        except ArtifactNotFoundError:
            await send(
                self._error(
                    request.request_id,
                    "artifact_not_found",
                    "Artifact not found",
                )
            )
        except ArtifactDownloadTooLargeError:
            await send(
                self._error(
                    request.request_id,
                    "artifact_too_large",
                    "Artifact exceeds download limit",
                )
            )
        except Exception:
            await send(
                self._error(
                    request.request_id,
                    "internal_error",
                    "Request failed",
                )
            )
