"""Supervised stdio client for the Codex App Server JSONL protocol."""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import AsyncIterator, Mapping, Sequence
from typing import Any


_CLOSED = object()


class AppServerProtocolError(RuntimeError):
    pass


class CodexAppServerClient:
    """One initialized App Server connection with bounded lossless queues."""

    def __init__(
        self,
        command: Sequence[str],
        *,
        cwd: str | None = None,
        env: Mapping[str, str] | None = None,
        request_timeout_seconds: float = 120.0,
        max_line_bytes: int = 4 * 1024 * 1024,
        max_event_queue: int = 1024,
    ) -> None:
        if not command:
            raise ValueError("Codex App Server command must not be empty")
        self._command = tuple(command)
        self._cwd = cwd or None
        self._env = dict(env or {})
        self._request_timeout = request_timeout_seconds
        self._max_line_bytes = max_line_bytes
        self._events: asyncio.Queue[dict[str, Any] | object] = asyncio.Queue(
            maxsize=max_event_queue
        )
        self._pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self._next_id = 1
        self._process: asyncio.subprocess.Process | None = None
        self._reader: asyncio.Task[None] | None = None
        self._stderr: asyncio.Task[None] | None = None
        self._write_lock = asyncio.Lock()
        self._fatal: BaseException | None = None

    async def start(self) -> None:
        if self._process is not None:
            return
        environment = os.environ.copy()
        environment.update(self._env)
        process = await asyncio.create_subprocess_exec(
            *self._command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=self._cwd,
            env=environment,
            limit=self._max_line_bytes + 1,
        )
        self._process = process
        self._reader = asyncio.create_task(
            self._read_stdout(process), name="knoa-codex-app-server-stdout"
        )
        self._stderr = asyncio.create_task(
            self._drain_stderr(process), name="knoa-codex-app-server-stderr"
        )
        try:
            await self.request(
                "initialize",
                {
                    "clientInfo": {
                        "name": "knoa-platform",
                        "title": "Knoa Platform",
                        "version": "1.0.0",
                    }
                },
            )
            await self.notify("initialized", {})
        except BaseException:
            await self.close()
            raise

    async def request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        if self._process is None:
            raise AppServerProtocolError("Codex App Server is not started")
        request_id = self._next_id
        self._next_id += 1
        future = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        try:
            await self._send({"method": method, "id": request_id, "params": params})
            response = await asyncio.wait_for(future, timeout=self._request_timeout)
        finally:
            self._pending.pop(request_id, None)
        error = response.get("error")
        if isinstance(error, dict):
            raise AppServerProtocolError(
                f"{method} failed: {error.get('message') or error.get('code') or 'unknown error'}"
            )
        result = response.get("result", {})
        if not isinstance(result, dict):
            raise AppServerProtocolError(f"{method} returned a non-object result")
        return result

    async def notify(self, method: str, params: dict[str, Any]) -> None:
        await self._send({"method": method, "params": params})

    async def respond(
        self,
        request_id: int | str,
        *,
        result: dict[str, Any] | None = None,
        error: dict[str, Any] | None = None,
    ) -> None:
        message: dict[str, Any] = {"id": request_id}
        if error is not None:
            message["error"] = error
        else:
            message["result"] = result or {}
        await self._send(message)

    async def events(self) -> AsyncIterator[dict[str, Any]]:
        while True:
            item = await self._events.get()
            if item is _CLOSED:
                if self._fatal is not None:
                    raise AppServerProtocolError(str(self._fatal)) from self._fatal
                return
            assert isinstance(item, dict)
            yield item

    async def close(self) -> None:
        process, self._process = self._process, None
        if process is not None and process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=3.0)
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
        tasks = tuple(task for task in (self._reader, self._stderr) if task is not None)
        self._reader = None
        self._stderr = None
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._finish(AppServerProtocolError("Codex App Server connection closed"))

    async def _send(self, message: dict[str, Any]) -> None:
        process = self._process
        if process is None or process.stdin is None or process.returncode is not None:
            raise AppServerProtocolError("Codex App Server is unavailable")
        encoded = json.dumps(message, ensure_ascii=False, separators=(",", ":")).encode(
            "utf-8"
        ) + b"\n"
        if len(encoded) > self._max_line_bytes:
            raise AppServerProtocolError("Codex App Server request exceeds line limit")
        async with self._write_lock:
            process.stdin.write(encoded)
            await process.stdin.drain()

    async def _read_stdout(self, process: asyncio.subprocess.Process) -> None:
        assert process.stdout is not None
        try:
            while True:
                line = await process.stdout.readline()
                if not line:
                    code = await process.wait()
                    raise AppServerProtocolError(
                        f"Codex App Server exited with status {code}"
                    )
                if len(line) > self._max_line_bytes or not line.endswith(b"\n"):
                    raise AppServerProtocolError(
                        "Codex App Server response exceeds line limit"
                    )
                try:
                    message = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise AppServerProtocolError(
                        "Codex App Server emitted invalid JSON"
                    ) from exc
                if not isinstance(message, dict):
                    raise AppServerProtocolError(
                        "Codex App Server emitted a non-object message"
                    )
                request_id = message.get("id")
                if request_id is not None and "method" not in message:
                    future = self._pending.get(request_id)
                    if future is not None and not future.done():
                        future.set_result(message)
                    continue
                try:
                    self._events.put_nowait(message)
                except asyncio.QueueFull as exc:
                    raise AppServerProtocolError(
                        "Codex App Server event queue overflowed"
                    ) from exc
        except asyncio.CancelledError:
            raise
        except BaseException as exc:
            self._finish(exc)

    @staticmethod
    async def _drain_stderr(process: asyncio.subprocess.Process) -> None:
        assert process.stderr is not None
        while await process.stderr.readline():
            pass

    def _finish(self, exc: BaseException) -> None:
        if self._fatal is None:
            self._fatal = exc
        for future in self._pending.values():
            if not future.done():
                future.set_exception(AppServerProtocolError(str(exc)))
        try:
            self._events.put_nowait(_CLOSED)
        except asyncio.QueueFull:
            try:
                self._events.get_nowait()
            except asyncio.QueueEmpty:
                pass
            self._events.put_nowait(_CLOSED)
