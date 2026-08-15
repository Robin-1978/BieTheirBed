"""Core API v1 WebSocket connection handler."""
from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from pydantic import ValidationError
from websockets.exceptions import ConnectionClosed

from knoa_platform.agent_runtime.artifact_service import (
    ArtifactDownloadTooLargeError,
    ArtifactNotFoundError,
    InvalidArtifactError,
)
from knoa_platform.agent_runtime.contracts import (
    ArtifactServicePort,
    ArtifactTranscriptionServicePort,
    ControlServicePort,
)
from knoa_platform.agent_runtime.transcription_service import (
    InvalidAudioArtifactError,
    TranscriptionFailedError,
    TranscriptionUnavailableError,
)
from knoa_platform.automation import (
    ScheduleService,
    TriggerService,
)
from knoa_platform.automation.repository import (
    ScheduleIdempotencyConflictError,
    ScheduleNotFoundError,
    ScheduleTransitionError,
)
from knoa_platform.automation.trigger_repository import (
    TriggerIdempotencyConflictError,
    TriggerNotFoundError,
    TriggerTransitionError,
)
from knoa_platform.conversation import (
    ChatTurnConflictError,
    ChatTurnNotFoundError,
    ConversationService,
    ConversationSessionConflictError,
    ConversationSessionNotFoundError,
)
from knoa_platform.configuration import ConfigApplyError, ConfigConflictError
from knoa_platform.exceptions import SessionNotFoundError
from knoa_platform.service.core_artifact_commands import ArtifactCommandHandler
from knoa_platform.service.core_api import (
    AuthenticatedMessage,
    AuthenticateRequest,
    ChatTurnSignalMessage,
    ChatTurnSnapshot,
    ChatTurnSubscribedMessage,
    CoreError,
    PrincipalTaskEventMessage,
    PrincipalTaskEventsSubscribedMessage,
    SubscribeChatTurnRequest,
    SubscribePrincipalTaskEventsRequest,
    SubscribeTaskRequest,
    TaskEventMessage,
    TaskSubscribedMessage,
    UnsubscribeRequest,
    UnsubscribedMessage,
    parse_core_request_json,
)
from knoa_platform.service.core_auth import (
    PrincipalAuthenticator,
)
from knoa_platform.service.core_automation_commands import AutomationCommandHandler
from knoa_platform.service.core_conversation_commands import ConversationCommandHandler
from knoa_platform.service.core_configuration_commands import (
    ConfigurationCommandHandler,
)
from knoa_platform.service.core_interaction_commands import HumanInteractionCommandHandler
from knoa_platform.service.core_mcp_commands import MCPPackageCommandHandler
from knoa_platform.service.core_task_commands import TaskCommandHandler
from knoa_platform.tasks import (
    TaskCapacityError,
    TaskIdempotencyConflictError,
    TaskNotFoundError,
    TaskService,
    TaskTransitionError,
)
from knoa_platform.interactions import HumanInteractionService

logger = logging.getLogger(__name__)


class WebSocketConnection(Protocol):
    async def recv(self) -> str | bytes: ...
    async def send(self, message: str) -> None: ...
    def __aiter__(self): ...


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
        conversations: ConversationService | None = None,
        transcription: ArtifactTranscriptionServicePort | None = None,
        interactions: HumanInteractionService | None = None,
        mcp_packages: Any | None = None,
        sessions: Any | None = None,
        owner_principal_id: str = "",
        configuration: Any | None = None,
        generation_states: Callable[[], tuple[Any, ...]] | None = None,
        policy_preview: Callable[[str, Any], Awaitable[Any]] | None = None,
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
        self._conversations = conversations
        self._transcription = transcription
        self._authenticator = authenticator
        self._authentication_timeout = max(0.01, authentication_timeout_seconds)
        self._max_subscriptions = max_subscriptions_per_connection
        command_handlers: list[Any] = [
            HumanInteractionCommandHandler(interactions),
            ConversationCommandHandler(control, conversations),
            TaskCommandHandler(tasks, schedules, triggers),
            AutomationCommandHandler(schedules, triggers),
            ArtifactCommandHandler(artifacts, transcription),
        ]
        if (
            configuration is not None
            and generation_states is not None
            and policy_preview is not None
            and owner_principal_id
        ):
            command_handlers.insert(
                0,
                ConfigurationCommandHandler(
                    configuration,
                    owner_principal_id=owner_principal_id,
                    generation_states=generation_states,
                    policy_preview=policy_preview,
                ),
            )
        if mcp_packages is not None and sessions is not None and owner_principal_id:
            command_handlers.append(
                MCPPackageCommandHandler(
                    mcp_packages,
                    sessions,
                    owner_principal_id=owner_principal_id,
                )
            )
        self._command_handlers = tuple(command_handlers)

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
                if isinstance(request, UnsubscribeRequest):
                    subscription = subscriptions.pop(
                        request.subscription_request_id,
                        None,
                    )
                    released = subscription is not None
                    if subscription is not None and not subscription.done():
                        subscription.cancel()
                        await asyncio.gather(subscription, return_exceptions=True)
                    await send(
                        UnsubscribedMessage(
                            request_id=request.request_id,
                            subscription_request_id=(
                                request.subscription_request_id
                            ),
                            released=released,
                        )
                    )
                    continue
                if isinstance(
                    request,
                    (
                        SubscribeTaskRequest,
                        SubscribePrincipalTaskEventsRequest,
                        SubscribeChatTurnRequest,
                    ),
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
                    if isinstance(request, SubscribeTaskRequest):
                        stream = self._stream_task(principal, request, send)
                    elif isinstance(request, SubscribeChatTurnRequest):
                        stream = self._stream_chat_turn(principal, request, send)
                    else:
                        stream = self._stream_principal_tasks(principal, request, send)
                    subscription = asyncio.create_task(stream)
                    subscriptions[request.request_id] = subscription
                    subscription.add_done_callback(
                        lambda _task, request_id=request.request_id: (
                            subscriptions.pop(request_id, None)
                        )
                    )
                    continue
                await self._dispatch_scalar(principal, request, send)
        except ConnectionClosed:
            return
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
            logger.exception(
                "Core request failed method=%s request_id=%s",
                getattr(request, "method", "unknown"),
                getattr(request, "request_id", "unknown"),
            )
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

    async def _stream_chat_turn(
        self,
        principal: str,
        request: SubscribeChatTurnRequest,
        send: Callable[[Any], Awaitable[None]],
    ) -> None:
        if self._conversations is None:
            await send(
                self._error(
                    request.request_id,
                    "invalid_request",
                    "Conversation service is unavailable",
                )
            )
            return
        try:
            await self._conversations.get_turn(principal, request.turn_id)
            await send(
                ChatTurnSubscribedMessage(
                    request_id=request.request_id,
                    turn_id=request.turn_id,
                )
            )
            async for signal in self._conversations.updates(
                principal,
                request.turn_id,
            ):
                await send(
                    ChatTurnSignalMessage(
                        request_id=request.request_id,
                        turn=ChatTurnSnapshot.from_record(signal.turn),
                    )
                )
        except ChatTurnNotFoundError:
            await send(
                self._error(
                    request.request_id,
                    "chat_turn_not_found",
                    "ChatTurn not found",
                )
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            await send(
                self._error(
                    request.request_id,
                    "internal_error",
                    "ChatTurn subscription failed",
                )
            )

    async def _dispatch_scalar(
        self,
        principal: str,
        request: Any,
        send: Callable[[Any], Awaitable[None]],
    ) -> None:
        try:
            for handler in self._command_handlers:
                if await handler.dispatch(principal, request, send):
                    return
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
        except ChatTurnNotFoundError:
            await send(
                self._error(
                    request.request_id,
                    "chat_turn_not_found",
                    "ChatTurn not found",
                )
            )
        except ChatTurnConflictError:
            await send(
                self._error(
                    request.request_id,
                    "invalid_request",
                    "ChatTurn request ID conflicts with an existing turn",
                )
            )
        except ConversationSessionNotFoundError:
            await send(
                self._error(
                    request.request_id,
                    "session_not_found",
                    "Conversation session not found",
                )
            )
        except ConversationSessionConflictError:
            await send(
                self._error(
                    request.request_id,
                    "invalid_request",
                    "Conversation session conflicts with the requested operation",
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
        except TranscriptionUnavailableError:
            await send(
                self._error(
                    request.request_id,
                    "capability_denied",
                    "Audio transcription is unavailable",
                )
            )
        except InvalidAudioArtifactError:
            await send(
                self._error(
                    request.request_id,
                    "invalid_request",
                    "Invalid audio artifact",
                )
            )
        except TranscriptionFailedError:
            await send(
                self._error(
                    request.request_id,
                    "provider_failed",
                    "Audio transcription failed",
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
        except ConfigConflictError:
            await send(
                self._error(
                    request.request_id,
                    "config_conflict",
                    "Configuration changed concurrently",
                )
            )
        except ConfigApplyError:
            await send(
                self._error(
                    request.request_id,
                    "config_apply_failed",
                    "Configuration validation or preflight failed",
                )
            )
        except LookupError:
            await send(
                self._error(
                    request.request_id,
                    "config_not_found",
                    "Configuration revision or draft not found",
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
            logger.exception(
                "Core request failed method=%s request_id=%s",
                getattr(request, "method", "unknown"),
                getattr(request, "request_id", "unknown"),
            )
            await send(
                self._error(
                    request.request_id,
                    "internal_error",
                    "Request failed",
                )
            )
