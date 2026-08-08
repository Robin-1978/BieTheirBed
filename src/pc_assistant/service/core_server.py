"""Core API v1 WebSocket connection handler."""
from __future__ import annotations

import asyncio
import hmac
import uuid
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from pydantic import ValidationError

from pc_assistant.agent_runtime.contracts import (
    ArtifactAttachment,
    ArtifactDownloadRequest,
    ArtifactServicePort,
    ArtifactUploadRequest,
    CancelRequest,
    ConfigSetRequest,
    ControlServicePort,
    RunRequest,
    RuntimeScope,
)
from pc_assistant.agent_runtime.artifact_service import (
    ArtifactDownloadTooLargeError,
    ArtifactNotFoundError,
    InvalidArtifactError,
)
from pc_assistant.agent_runtime.core_application import CoreApplication
from pc_assistant.agent_runtime.run_registry import RunCapacityExceededError
from pc_assistant.exceptions import SessionNotFoundError
from pc_assistant.service.core_api import (
    AuthenticateRequest,
    AuthenticatedMessage,
    ArtifactDownloadedMessage,
    ArtifactUploadedMessage,
    CancelResultMessage,
    CancelRunRequest,
    ClearMemoryRequest,
    ConfigSetMessage,
    ConfirmationRequestedMessage,
    ConfirmationResolvedMessage,
    CoreError,
    CreateSessionRequest,
    DownloadArtifactRequest,
    GetHistoryRequest,
    GetStatusRequest,
    HealthMessage,
    HealthRequest,
    HistoryMessage,
    ListMemoryRequest,
    ListToolsRequest,
    MemoryClearedMessage,
    MemoryListMessage,
    RunAcceptedMessage,
    ResolveConfirmationRequest,
    SessionCreatedMessage,
    SetConfigRequest,
    StartRunRequest,
    StatusMessage,
    ToolsMessage,
    UploadArtifactRequest,
    parse_core_request_json,
)
from pc_assistant.service.credentials import verify_principal_credential
from pc_assistant.agent_runtime.tool_step import ProposedToolCall


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


class ConnectionConfirmationPort:
    """Route confirmations only through the connection that initiated a run."""

    def __init__(
        self,
        send: Callable[[Any], Awaitable[None]],
        *,
        timeout_seconds: float = 120.0,
    ) -> None:
        self._send = send
        self._timeout = max(1.0, timeout_seconds)
        self._pending: dict[str, asyncio.Future[bool]] = {}

    async def confirm(
        self,
        scope: RuntimeScope,
        run_id: str,
        call: ProposedToolCall,
        reason: str,
    ) -> bool:
        confirmation_id = uuid.uuid4().hex
        future = asyncio.get_running_loop().create_future()
        self._pending[confirmation_id] = future
        try:
            await self._send(
                ConfirmationRequestedMessage(
                    request_id=f"confirmation-{confirmation_id}",
                    confirmation_id=confirmation_id,
                    run_id=run_id,
                    session_handle=scope.session_handle,
                    tool_call_id=call.call_id,
                    tool_name=call.name,
                    arguments=call.arguments,
                    reason=reason,
                )
            )
            return await asyncio.wait_for(future, timeout=self._timeout)
        except asyncio.CancelledError:
            raise
        except Exception:
            return False
        finally:
            self._pending.pop(confirmation_id, None)

    def resolve(self, confirmation_id: str, approved: bool) -> bool:
        future = self._pending.get(confirmation_id)
        if future is None or future.done():
            return False
        future.set_result(approved)
        return True

    def close(self) -> None:
        for future in tuple(self._pending.values()):
            if not future.done():
                future.set_result(False)
        self._pending.clear()


class CoreServer:
    def __init__(
        self,
        application: CoreApplication,
        control: ControlServicePort,
        artifacts: ArtifactServicePort,
        authenticator: PrincipalAuthenticator,
        *,
        authentication_timeout_seconds: float = 10.0,
        max_active_runs_per_connection: int = 8,
    ) -> None:
        if max_active_runs_per_connection < 1:
            raise ValueError("Active run limit must be at least one")
        self._application = application
        self._control = control
        self._artifacts = artifacts
        self._authenticator = authenticator
        self._authentication_timeout = max(0.01, authentication_timeout_seconds)
        self._max_active_runs = max_active_runs_per_connection

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
        run_tasks: dict[str, asyncio.Task[None]] = {}
        run_ids: dict[str, str] = {}
        principal = ""

        async def send(message: Any) -> None:
            async with send_lock:
                await websocket.send(message.model_dump_json())

        confirmations = ConnectionConfirmationPort(send)

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
                await send(self._error("unknown", "invalid_request", "Invalid authentication request"))
                return
            if not isinstance(first, AuthenticateRequest):
                await send(self._error(first.request_id, "unauthenticated", "Authentication required"))
                return
            principal = await self._authenticator.authenticate(first.credential)
            if principal is None:
                await send(self._error(first.request_id, "unauthenticated", "Authentication failed"))
                return
            await send(AuthenticatedMessage(request_id=first.request_id))

            async for raw in websocket:
                try:
                    request = parse_core_request_json(raw)
                except (ValidationError, ValueError, TypeError):
                    await send(self._error("unknown", "invalid_request", "Invalid request"))
                    continue
                if isinstance(request, AuthenticateRequest):
                    await send(self._error(request.request_id, "invalid_request", "Already authenticated"))
                    continue
                if isinstance(request, StartRunRequest):
                    if request.request_id in run_tasks:
                        await send(self._error(request.request_id, "invalid_request", "Duplicate request ID"))
                        continue
                    if len(run_tasks) >= self._max_active_runs:
                        await send(
                            self._error(
                                request.request_id,
                                "resource_exhausted",
                                "Active run limit reached",
                            )
                        )
                        continue
                    task = asyncio.create_task(
                        self._stream_run(
                            principal,
                            request,
                            send,
                            run_ids,
                            confirmations,
                        )
                    )
                    run_tasks[request.request_id] = task
                    task.add_done_callback(lambda _task, rid=request.request_id: run_tasks.pop(rid, None))
                    continue
                if isinstance(request, ResolveConfirmationRequest):
                    resolved = confirmations.resolve(
                        request.confirmation_id,
                        request.approved,
                    )
                    await send(
                        ConfirmationResolvedMessage(
                            request_id=request.request_id,
                            resolved=resolved,
                        )
                    )
                    continue
                await self._dispatch_scalar(principal, request, send)
        finally:
            confirmations.close()
            for run_id in tuple(run_ids.values()):
                if not principal:
                    break
                try:
                    await self._application.cancel(
                        principal,
                        CancelRequest(run_id=run_id, reason="connection_lost"),
                    )
                except Exception:
                    pass
            for task in tuple(run_tasks.values()):
                if not task.done():
                    task.cancel()
            if run_tasks:
                await asyncio.gather(*run_tasks.values(), return_exceptions=True)

    async def _stream_run(
        self,
        principal: str,
        request: StartRunRequest,
        send,
        run_ids,
        confirmations: ConnectionConfirmationPort,
    ) -> None:
        internal = RunRequest(
            client_request_id=request.request_id,
            input=request.input,
            attachments=tuple(
                ArtifactAttachment(
                    artifact_id=item.artifact_id,
                    caption=item.caption,
                )
                for item in request.attachments
            ),
            tools_enabled=request.tools_enabled,
        )
        try:
            accepted = False
            async for event in self._application.run(
                principal,
                request.session_handle,
                internal,
                confirmation=confirmations,
            ):
                if not accepted:
                    run_ids[request.request_id] = event.run_id
                    await send(RunAcceptedMessage(request_id=request.request_id, run_id=event.run_id))
                    accepted = True
                await send(event)
        except SessionNotFoundError:
            await send(self._error(request.request_id, "session_not_found", "Session not found"))
        except RunCapacityExceededError:
            await send(
                self._error(
                    request.request_id,
                    "resource_exhausted",
                    "Global active run limit reached",
                )
            )
        except Exception:
            await send(self._error(request.request_id, "internal_error", "Run dispatch failed"))
        finally:
            run_ids.pop(request.request_id, None)

    async def _dispatch_scalar(self, principal: str, request, send) -> None:
        try:
            if isinstance(request, CreateSessionRequest):
                scope = await self._control.create_session(principal)
                await send(SessionCreatedMessage(
                    request_id=request.request_id,
                    session_handle=scope.session_handle,
                ))
            elif isinstance(request, HealthRequest):
                await send(HealthMessage(
                    request_id=request.request_id,
                    result=await self._application.health_check(),
                ))
            elif isinstance(request, GetStatusRequest):
                scope = RuntimeScope(principal_id=principal, session_handle=request.session_handle)
                await send(StatusMessage(request_id=request.request_id, result=await self._control.get_status(scope)))
            elif isinstance(request, GetHistoryRequest):
                scope = RuntimeScope(principal_id=principal, session_handle=request.session_handle)
                await send(HistoryMessage(request_id=request.request_id, result=await self._control.get_history(scope)))
            elif isinstance(request, ListMemoryRequest):
                scope = RuntimeScope(principal_id=principal, session_handle=request.session_handle)
                await send(MemoryListMessage(request_id=request.request_id, result=await self._control.list_memory(scope)))
            elif isinstance(request, ClearMemoryRequest):
                scope = RuntimeScope(principal_id=principal, session_handle=request.session_handle)
                await send(MemoryClearedMessage(request_id=request.request_id, result=await self._control.clear_memory(scope)))
            elif isinstance(request, ListToolsRequest):
                scope = RuntimeScope(principal_id=principal, session_handle=request.session_handle)
                await send(ToolsMessage(request_id=request.request_id, result=await self._control.list_tools(scope)))
            elif isinstance(request, SetConfigRequest):
                scope = RuntimeScope(principal_id=principal, session_handle=request.session_handle)
                result = await self._control.set_config(
                    scope,
                    ConfigSetRequest(field_name=request.field_name, value=request.value),
                )
                await send(ConfigSetMessage(request_id=request.request_id, result=result))
            elif isinstance(request, CancelRunRequest):
                result = await self._application.cancel(
                    principal,
                    CancelRequest(run_id=request.run_id, reason=request.reason),
                )
                await send(CancelResultMessage(request_id=request.request_id, result=result))
            elif isinstance(request, UploadArtifactRequest):
                scope = RuntimeScope(principal_id=principal, session_handle=request.session_handle)
                result = await self._artifacts.upload(
                    scope,
                    ArtifactUploadRequest(
                        data_url=request.data_url,
                        media_type=request.media_type,
                        caption=request.caption,
                    ),
                )
                await send(ArtifactUploadedMessage(request_id=request.request_id, result=result))
            elif isinstance(request, DownloadArtifactRequest):
                scope = RuntimeScope(principal_id=principal, session_handle=request.session_handle)
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
                await send(self._error(request.request_id, "invalid_request", "Unsupported request"))
        except SessionNotFoundError:
            await send(self._error(request.request_id, "session_not_found", "Session not found"))
        except PermissionError:
            await send(self._error(request.request_id, "capability_denied", "Capability denied"))
        except InvalidArtifactError:
            await send(self._error(request.request_id, "invalid_request", "Invalid artifact"))
        except ArtifactNotFoundError:
            await send(self._error(request.request_id, "artifact_not_found", "Artifact not found"))
        except ArtifactDownloadTooLargeError:
            await send(self._error(request.request_id, "artifact_too_large", "Artifact exceeds download limit"))
        except Exception:
            await send(self._error(request.request_id, "internal_error", "Request failed"))
