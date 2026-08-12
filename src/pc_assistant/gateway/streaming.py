"""Fail-closed HTTP/TLS surface for Secure Gateway mobile access."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from pydantic import ValidationError
from starlette.requests import Request
from starlette.responses import JSONResponse, StreamingResponse

from pc_assistant.gateway.protocol import (
    EventQuery,
    TaskEventQuery,
)

logger = logging.getLogger(__name__)
_MAX_BODY_BYTES = 16 * 1024



class GatewayStreaming:

    async def _chat_turn_stream(
        self,
        request: Request,
    ) -> JSONResponse | StreamingResponse:
        authenticated = self._authorize(request, limit=20)
        if isinstance(authenticated, JSONResponse):
            return authenticated
        turn_id = self._path_identifier(request, "turn_id")
        if turn_id is None:
            return JSONResponse({"error": "invalid_request"}, status_code=400)
        device_id = authenticated.device.device_id
        stream_key = (device_id, f"chat:{turn_id}")
        previous = self._stream_replacements.get(stream_key)
        if previous is not None:
            previous.set()
        replaced = asyncio.Event()
        self._stream_replacements[stream_key] = replaced
        self._active_event_streams[device_id] += 1
        token = self._bearer_token(request)
        principal_id = authenticated.device.principal_id

        async def stream():
            iterator = self._core.chat_turn_updates(principal_id, turn_id).__aiter__()
            pending: asyncio.Task[Any] | None = None
            replacement = asyncio.create_task(replaced.wait())
            try:
                pending = asyncio.create_task(anext(iterator))
                while True:
                    done, _pending = await asyncio.wait(
                        {pending, replacement},
                        timeout=self._event_heartbeat_seconds,
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    if replacement in done:
                        return
                    if not done:
                        if self._authenticate_token(token) is None:
                            return
                        yield b": keepalive\n\n"
                        continue
                    try:
                        turn = pending.result()
                    except StopAsyncIteration:
                        return
                    except Exception:
                        yield self._sse("error", {"error": "unavailable"})
                        return
                    if self._authenticate_token(token) is None:
                        return
                    yield self._sse(
                        "snapshot",
                        {"turn": turn.model_dump(mode="json")},
                    )
                    pending = asyncio.create_task(anext(iterator))
            finally:
                if pending is not None and not pending.done():
                    pending.cancel()
                    await asyncio.gather(pending, return_exceptions=True)
                if not replacement.done():
                    replacement.cancel()
                await asyncio.gather(replacement, return_exceptions=True)
                close = getattr(iterator, "aclose", None)
                if close is not None:
                    await close()
                if self._stream_replacements.get(stream_key) is replaced:
                    self._stream_replacements.pop(stream_key, None)
                remaining = self._active_event_streams.get(device_id, 1) - 1
                if remaining > 0:
                    self._active_event_streams[device_id] = remaining
                else:
                    self._active_event_streams.pop(device_id, None)

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache, no-store",
                "X-Accel-Buffering": "no",
            },
        )

    async def _task_execution_events(self, request: Request) -> JSONResponse:
        authenticated = self._authorize(request, limit=120)
        if isinstance(authenticated, JSONResponse):
            return authenticated
        execution_id = self._path_identifier(request, "execution_id")
        if execution_id is None:
            return JSONResponse({"error": "invalid_request"}, status_code=400)
        try:
            query = TaskEventQuery.model_validate(dict(request.query_params))
            events = await self._core.task_events(
                authenticated.device.principal_id,
                execution_id,
                after_seq=query.after_seq,
            )
        except ValidationError:
            return JSONResponse({"error": "invalid_request"}, status_code=400)
        except Exception as exc:
            return self._core_error(exc)
        return JSONResponse(
            {"events": [event.model_dump(mode="json") for event in events]}
        )

    async def _events(self, request: Request) -> JSONResponse | StreamingResponse:
        authenticated = self._authorize(request, limit=20)
        if isinstance(authenticated, JSONResponse):
            return authenticated
        try:
            query = EventQuery.model_validate(dict(request.query_params))
            after_id = self._event_cursor(request, query.after_id)
        except (ValidationError, ValueError):
            return JSONResponse({"error": "invalid_request"}, status_code=400)
        device_id = authenticated.device.device_id
        stream_key = (device_id, "principal-events")
        previous = self._stream_replacements.get(stream_key)
        if previous is not None:
            previous.set()
        replaced = asyncio.Event()
        self._stream_replacements[stream_key] = replaced
        self._active_event_streams[device_id] += 1
        self._record_audit(
            "stream_opened",
            request=request,
            device_id=device_id,
            principal_id=authenticated.device.principal_id,
        )
        token = self._bearer_token(request)
        principal_id = authenticated.device.principal_id

        async def stream():
            iterator = self._core.principal_task_events(
                principal_id,
                after_id=after_id,
            ).__aiter__()
            pending: asyncio.Task[Any] | None = None
            replacement = asyncio.create_task(replaced.wait())
            try:
                pending = asyncio.create_task(anext(iterator))
                while True:
                    done, _pending = await asyncio.wait(
                        {pending, replacement},
                        timeout=self._event_heartbeat_seconds,
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    if replacement in done:
                        return
                    if not done:
                        if self._authenticate_token(token) is None:
                            return
                        yield b": keepalive\n\n"
                        continue
                    try:
                        feed_event = pending.result()
                    except StopAsyncIteration:
                        return
                    except Exception:
                        logger.warning(
                            "Secure Gateway event stream lost",
                            exc_info=True,
                        )
                        yield self._sse("error", {"error": "unavailable"})
                        return
                    if self._authenticate_token(token) is None:
                        return
                    yield self._sse(
                        feed_event.event.event_type,
                        {
                            "feed_event_id": feed_event.feed_event_id,
                            "event": feed_event.event.model_dump(mode="json"),
                        },
                        event_id=feed_event.feed_event_id,
                    )
                    pending = asyncio.create_task(anext(iterator))
            finally:
                if pending is not None and not pending.done():
                    pending.cancel()
                    await asyncio.gather(pending, return_exceptions=True)
                if not replacement.done():
                    replacement.cancel()
                await asyncio.gather(replacement, return_exceptions=True)
                close = getattr(iterator, "aclose", None)
                if close is not None:
                    await close()
                if self._stream_replacements.get(stream_key) is replaced:
                    self._stream_replacements.pop(stream_key, None)
                remaining = self._active_event_streams.get(device_id, 1) - 1
                if remaining > 0:
                    self._active_event_streams[device_id] = remaining
                else:
                    self._active_event_streams.pop(device_id, None)

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache, no-store",
                "X-Accel-Buffering": "no",
            },
        )
