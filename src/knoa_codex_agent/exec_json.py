"""Client for the Codex CLI ``exec --json`` JSONL protocol."""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import AsyncIterator, Mapping, Sequence
from typing import Any


_CLOSED = object()


class ExecJsonProtocolError(RuntimeError):
    pass


class CodexExecJsonClient:
    """One supervised ``codex exec --json`` process.

    The CLI writes one JSON object per line to stdout.  A small prefetch buffer
    lets the runtime wait for ``thread.started`` before exposing a turn while
    preserving the event for the consumer.
    """

    def __init__(
        self,
        command: Sequence[str],
        *,
        cwd: str | None = None,
        env: Mapping[str, str] | None = None,
        sandbox: str = "read-only",
        approval_policy: str = "never",
        model: str = "",
        request_timeout_seconds: float = 600.0,
        max_line_bytes: int = 4 * 1024 * 1024,
        max_event_queue: int = 1024,
    ) -> None:
        if not command:
            raise ValueError("Codex exec command must not be empty")
        self._command = tuple(command)
        self._cwd = cwd or None
        self._env = dict(env or {})
        self._sandbox = sandbox
        self._approval_policy = approval_policy
        self._model = model.strip()
        self._timeout = request_timeout_seconds
        self._max_line_bytes = max_line_bytes
        self._events: asyncio.Queue[dict[str, Any] | object] = asyncio.Queue(
            maxsize=max_event_queue
        )
        self._prefetched: list[dict[str, Any]] = []
        self._process: asyncio.subprocess.Process | None = None
        self._reader: asyncio.Task[None] | None = None
        self._stderr: asyncio.Task[None] | None = None
        self._fatal: BaseException | None = None
        self.stderr_tail = ""

    async def start(self, prompt: str, *, thread_id: str | None = None) -> None:
        if self._process is not None:
            raise ExecJsonProtocolError("Codex exec process is already started")
        command = list(self._command)
        # Accept either [codex] or [codex, exec] in configuration.
        if command and command[-1] == "app-server":
            command.pop()
        if not command or command[-1] != "exec":
            command.append("exec")
        command.extend(["resume"] if thread_id else [])
        command.extend(["--skip-git-repo-check", "--json"])
        if not thread_id:
            command.extend(["--sandbox", self._sandbox])
        command.extend(["-c", f'approval_policy="{self._approval_policy}"'])
        if self._model:
            command.extend(["--model", self._model])
        if thread_id:
            command.extend([thread_id, "-"])
        environment = os.environ.copy()
        environment.update(self._env)
        process = await asyncio.create_subprocess_exec(
            *command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=self._cwd,
            env=environment,
            limit=self._max_line_bytes + 1,
        )
        self._process = process
        self._reader = asyncio.create_task(self._read_stdout(process))
        self._stderr = asyncio.create_task(self._read_stderr(process))
        assert process.stdin is not None
        process.stdin.write(prompt.encode("utf-8"))
        if not prompt.endswith("\n"):
            process.stdin.write(b"\n")
        await process.stdin.drain()
        process.stdin.close()

    async def wait_thread_started(self) -> str:
        while True:
            event = await self._next_event()
            self._prefetched.append(event)
            if event.get("type") == "thread.started":
                thread_id = str(
                    event.get("thread_id") or event.get("thread", {}).get("id") or ""
                )
                if thread_id:
                    return thread_id
            if event.get("type") in {"error", "turn.failed"}:
                raise ExecJsonProtocolError(str(event.get("message") or event))

    async def events(self) -> AsyncIterator[dict[str, Any]]:
        while self._prefetched:
            yield self._prefetched.pop(0)
        while True:
            try:
                event = await self._next_event()
            except ExecJsonProtocolError as exc:
                if str(exc) == "Codex exec closed without an event":
                    return
                raise
            yield event

    async def interrupt(self) -> None:
        process = self._process
        if process is not None and process.returncode is None:
            process.terminate()

    async def close(self) -> None:
        process, self._process = self._process, None
        if process is not None and process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=2)
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
        for task in (self._reader, self._stderr):
            if task is not None and not task.done():
                task.cancel()
        await asyncio.gather(
            *(task for task in (self._reader, self._stderr) if task),
            return_exceptions=True,
        )
        self._reader = self._stderr = None

    async def _next_event(self) -> dict[str, Any]:
        item = await asyncio.wait_for(self._events.get(), timeout=self._timeout)
        if item is _CLOSED:
            if self._fatal:
                raise ExecJsonProtocolError(str(self._fatal)) from self._fatal
            raise ExecJsonProtocolError("Codex exec closed without an event")
        assert isinstance(item, dict)
        return item

    async def _read_stdout(self, process: asyncio.subprocess.Process) -> None:
        assert process.stdout is not None
        try:
            while True:
                line = await process.stdout.readline()
                if not line:
                    break
                if len(line) > self._max_line_bytes:
                    raise ExecJsonProtocolError(
                        "Codex exec JSONL line exceeds configured limit"
                    )
                try:
                    value = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(value, dict):
                    await self._events.put(value)
        except asyncio.CancelledError:
            raise
        except BaseException as exc:
            self._fatal = exc
        finally:
            try:
                self._events.put_nowait(_CLOSED)
            except asyncio.QueueFull:
                pass

    async def _read_stderr(self, process: asyncio.subprocess.Process) -> None:
        assert process.stderr is not None
        chunks: list[str] = []
        while True:
            line = await process.stderr.readline()
            if not line:
                break
            chunks.append(line.decode("utf-8", "replace"))
            self.stderr_tail = "".join(chunks)[-4000:]
