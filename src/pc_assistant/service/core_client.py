"""Thin Core API v1 WebSocket client."""
from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any, Protocol

import websockets

from pc_assistant.agent_runtime.contracts import (
    ArtifactDownloadResult,
    ConfigSetResult,
    HealthStatus,
    HistoryResult,
    MemoryClearResult,
    MemoryListResult,
    RunEvent,
    RuntimeStatus,
    ToolListResult,
)
from pc_assistant.artifacts import ArtifactRef
from pc_assistant.service.core_api import (
    AuthenticateRequest,
    AuthenticatedMessage,
    ArtifactDownloadedMessage,
    ArtifactInputRef,
    ArtifactUploadedMessage,
    CancelResultMessage,
    CancelRunRequest,
    CORE_WS_MAX_SIZE,
    CoreError,
    CoreServerMessage,
    CreateSessionRequest,
    DownloadArtifactRequest,
    ClearMemoryRequest,
    ConfigSetMessage,
    ConfirmationRequestedMessage,
    ConfirmationResolvedMessage,
    GetStatusRequest,
    GetHistoryRequest,
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
    parse_core_server_message_json,
)


class ClientWebSocket(Protocol):
    async def send(self, message: str) -> None: ...
    async def close(self) -> None: ...
    def __aiter__(self): ...


class CoreConnectionLostError(ConnectionError):
    pass


class CoreRunBufferOverflowError(CoreConnectionLostError):
    pass


class CoreRequestTimeoutError(TimeoutError):
    pass


class CoreRequestError(RuntimeError):
    def __init__(self, error: CoreError) -> None:
        self.code = error.code
        self.correlation_id = error.correlation_id
        super().__init__(error.message)


ConfirmationHandler = Callable[[ConfirmationRequestedMessage], Awaitable[bool]]


class CoreClient:
    def __init__(
        self,
        websocket: ClientWebSocket,
        *,
        confirmation_handler: ConfirmationHandler | None = None,
        request_timeout_seconds: float = 60.0,
        max_buffered_run_events: int = 256,
        max_pending_confirmations: int = 8,
    ) -> None:
        if max_buffered_run_events < 1:
            raise ValueError("Run event buffer limit must be at least one")
        if max_pending_confirmations < 1:
            raise ValueError("Pending confirmation limit must be at least one")
        self._websocket = websocket
        self._confirmation_handler = confirmation_handler
        self._request_timeout = max(0.01, request_timeout_seconds)
        self._max_buffered_run_events = max_buffered_run_events
        self._max_pending_confirmations = max_pending_confirmations
        self._reader_task: asyncio.Task[None] | None = None
        self._confirmation_tasks: set[asyncio.Task[None]] = set()
        self._pending: dict[str, asyncio.Future[CoreServerMessage]] = {}
        self._run_queues: dict[str, asyncio.Queue[RunEvent | Exception]] = {}
        self._active_runs: list[str] = []
        self._send_lock = asyncio.Lock()
        self._connected = False

    @classmethod
    async def connect(
        cls,
        uri: str,
        credential: str,
        *,
        confirmation_handler: ConfirmationHandler | None = None,
        request_timeout_seconds: float = 60.0,
        max_buffered_run_events: int = 256,
        max_pending_confirmations: int = 8,
    ) -> CoreClient:
        websocket = await websockets.connect(uri, max_size=CORE_WS_MAX_SIZE)
        client = cls(
            websocket,
            confirmation_handler=confirmation_handler,
            request_timeout_seconds=request_timeout_seconds,
            max_buffered_run_events=max_buffered_run_events,
            max_pending_confirmations=max_pending_confirmations,
        )
        await client.start(credential)
        return client

    @classmethod
    async def connect_unix(
        cls,
        path: str,
        credential: str = "local-transport",
        *,
        confirmation_handler: ConfirmationHandler | None = None,
        request_timeout_seconds: float = 60.0,
        max_buffered_run_events: int = 256,
        max_pending_confirmations: int = 8,
    ) -> CoreClient:
        websocket = await websockets.unix_connect(
            path=path,
            max_size=CORE_WS_MAX_SIZE,
        )
        client = cls(
            websocket,
            confirmation_handler=confirmation_handler,
            request_timeout_seconds=request_timeout_seconds,
            max_buffered_run_events=max_buffered_run_events,
            max_pending_confirmations=max_pending_confirmations,
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
        future: asyncio.Future[CoreServerMessage] = asyncio.get_running_loop().create_future()
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
            except TimeoutError as exc:
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
                if isinstance(message, ConfirmationRequestedMessage):
                    if len(self._confirmation_tasks) >= self._max_pending_confirmations:
                        failure = CoreConnectionLostError(
                            "Core pending confirmation limit exceeded"
                        )
                        await self._websocket.close()
                        break
                    task = asyncio.create_task(self._resolve_confirmation(message))
                    self._confirmation_tasks.add(task)
                    task.add_done_callback(self._confirmation_tasks.discard)
                    continue
                if isinstance(message, RunEvent):
                    queue = self._run_queues.get(message.run_id)
                    if queue is not None:
                        try:
                            queue.put_nowait(message)
                        except asyncio.QueueFull:
                            failure = CoreRunBufferOverflowError(
                                "Core run event buffer overflow"
                            )
                            await self._websocket.close()
                            break
                    continue
                if isinstance(message, RunAcceptedMessage):
                    future = self._pending.get(message.request_id)
                    if future is None or future.done():
                        failure = CoreConnectionLostError(
                            "Core protocol violation: unsolicited run acceptance"
                        )
                        await self._websocket.close()
                        break
                    self._run_queues.setdefault(
                        message.run_id,
                        asyncio.Queue(maxsize=self._max_buffered_run_events),
                    )
                future = self._pending.get(message.request_id)
                if future is not None and not future.done():
                    future.set_result(message)
        except asyncio.CancelledError:
            return
        except Exception as exc:
            failure = CoreConnectionLostError(f"Core connection lost: {type(exc).__name__}")
        finally:
            self._connected = False
            self._fail_all(failure)
            tasks, self._confirmation_tasks = self._confirmation_tasks, set()
            for task in tasks:
                task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)

    async def _resolve_confirmation(
        self,
        message: ConfirmationRequestedMessage,
    ) -> None:
        approved = False
        if self._confirmation_handler is not None:
            try:
                approved = bool(await self._confirmation_handler(message))
            except asyncio.CancelledError:
                raise
            except Exception:
                approved = False
        try:
            response = await self._request(
                ResolveConfirmationRequest(
                    request_id=self._request_id(),
                    confirmation_id=message.confirmation_id,
                    approved=approved,
                )
            )
            if not isinstance(response, ConfirmationResolvedMessage):
                raise RuntimeError(
                    "CoreServer returned an invalid confirmation response"
                )
        except (
            CoreConnectionLostError,
            CoreRequestError,
            CoreRequestTimeoutError,
        ):
            return

    def _fail_all(self, error: Exception) -> None:
        for future in tuple(self._pending.values()):
            if not future.done():
                future.set_exception(error)
        for queue in tuple(self._run_queues.values()):
            while not queue.empty():
                queue.get_nowait()
            queue.put_nowait(error)

    async def create_session(self) -> str:
        response = await self._request(
            CreateSessionRequest(request_id=self._request_id())
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
        caption: str = "",
    ) -> ArtifactRef:
        response = await self._request(
            UploadArtifactRequest(
                request_id=self._request_id(),
                session_handle=session_handle,
                data_url=data_url,
                media_type=media_type,
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
            raise RuntimeError("CoreServer returned an invalid artifact download response")
        return response.result

    async def run(
        self,
        session_handle: str,
        user_input: str = "",
        attachments: tuple[ArtifactInputRef, ...] = (),
        *,
        tools_enabled: bool = True,
    ) -> AsyncIterator[RunEvent]:
        response = await self._request(
            StartRunRequest(
                request_id=self._request_id(),
                session_handle=session_handle,
                input=user_input,
                attachments=attachments,
                tools_enabled=tools_enabled,
            )
        )
        if not isinstance(response, RunAcceptedMessage):
            raise RuntimeError("CoreServer returned an invalid run response")
        run_id = response.run_id
        queue = self._run_queues[run_id]
        self._active_runs.append(run_id)
        try:
            while True:
                item = await queue.get()
                if isinstance(item, Exception):
                    raise item
                yield item
                if item.is_terminal:
                    return
        finally:
            self._run_queues.pop(run_id, None)
            if run_id in self._active_runs:
                self._active_runs.remove(run_id)

    async def cancel(self, run_id: str) -> CancelResultMessage:
        response = await self._request(
            CancelRunRequest(request_id=self._request_id(), run_id=run_id)
        )
        if not isinstance(response, CancelResultMessage):
            raise RuntimeError("CoreServer returned an invalid cancel response")
        return response

    async def cancel_active(self) -> CancelResultMessage | None:
        if not self._active_runs:
            return None
        return await self.cancel(self._active_runs[-1])

    @property
    def is_connected(self) -> bool:
        return self._connected

    def set_confirmation_handler(
        self,
        handler: ConfirmationHandler | None,
    ) -> None:
        self._confirmation_handler = handler
