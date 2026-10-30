"""Read and control external Codex/OpenCode coding sessions.

The providers in this module do not own an external session.  They expose the
session store and concurrency rules of the application that does own it, so a
phone client can observe and continue the same conversation as a desktop or
CLI client.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

import httpx

from knoa_codex_agent.app_server import CodexAppServerClient


CodingSessionState = Literal[
    "idle",
    "running",
    "interrupted",
    "failed",
    "unknown",
]


class CodingSessionProviderError(RuntimeError):
    """Base error raised when an external coding session cannot be accessed."""


class CodingSessionNotFound(CodingSessionProviderError):
    """The provider does not contain the requested session."""


class CodingSessionConflict(CodingSessionProviderError):
    """The session is currently owned by another writer."""


class OpenCodeServiceUnavailable(CodingSessionProviderError):
    """The OpenCode background service of this user is not running."""


@dataclass(frozen=True, slots=True)
class OpenCodeEndpoint:
    """Where and how to reach one OpenCode v2 HTTP service.

    The password is kept out of ``repr`` so a log line or traceback cannot leak
    it.
    """

    base_url: str
    password: str = field(repr=False)
    username: str = "opencode"
    pid: int = 0


class OpenCodeEndpointSource(Protocol):
    """Resolves the OpenCode service that is current right now.

    ``None`` means no service is running; a problem with the registration
    itself raises a ``CodingSessionProviderError``.
    """

    def locate(self) -> OpenCodeEndpoint | None: ...


@dataclass(frozen=True, slots=True)
class CodingSessionMessage:
    message_id: str
    role: Literal["user", "assistant", "system", "tool"]
    text: str
    created_at: float


@dataclass(frozen=True, slots=True)
class CodingSessionSummary:
    provider: str
    external_session_id: str
    title: str
    directory: str
    state: CodingSessionState
    updated_at: float
    latest_user_message: str = ""
    latest_assistant_message: str = ""
    read_only: bool = False
    capabilities: tuple[str, ...] = ()
    native_state: str = ""
    revision: str = ""


@dataclass(frozen=True, slots=True)
class CodingSessionDetail:
    session: CodingSessionSummary
    messages: tuple[CodingSessionMessage, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class CodingSessionPage:
    sessions: tuple[CodingSessionSummary, ...]
    next_cursor: str = ""


@dataclass(frozen=True, slots=True)
class CodingSessionTurnReceipt:
    external_session_id: str
    external_turn_id: str
    accepted: bool
    read_only: bool = False


@dataclass(frozen=True, slots=True)
class CodingSessionEvent:
    kind: Literal[
        "assistant_delta",
        "status",
        "completed",
        "failed",
        "unknown",
    ]
    text: str = ""
    state: CodingSessionState = "unknown"
    error_code: str = ""


class CodingSessionProvider(Protocol):
    provider_id: str

    async def list_sessions(
        self,
        *,
        directory: str = "",
        search: str = "",
        limit: int = 50,
        cursor: str = "",
        include_archived: bool = False,
    ) -> CodingSessionPage: ...

    async def get_session(self, external_session_id: str) -> CodingSessionDetail: ...

    async def continue_session(
        self, external_session_id: str, text: str
    ) -> CodingSessionTurnReceipt: ...

    async def interrupt_session(
        self, external_session_id: str, external_turn_id: str = ""
    ) -> bool: ...

    async def close(self) -> None: ...


class CodingTurnHandle:
    """Own a live Codex app-server connection until its turn completes."""

    def __init__(
        self,
        client: CodexAppServerClient,
        external_session_id: str,
        external_turn_id: str,
    ) -> None:
        self._client = client
        self._session_id = external_session_id
        self.external_turn_id = external_turn_id
        self._closed = False

    async def events(self) -> AsyncIterator[CodingSessionEvent]:
        try:
            async for message in self._client.events():
                method = str(message.get("method") or "")
                params = message.get("params")
                if not isinstance(params, dict):
                    params = {}
                turn_id = str(params.get("turnId") or "")
                if turn_id and turn_id != self.external_turn_id:
                    continue
                if method == "item/agentMessage/delta":
                    yield CodingSessionEvent(
                        kind="assistant_delta",
                        text=str(params.get("delta") or ""),
                    )
                elif method in {"turn/completed", "turn_complete"}:
                    turn = params.get("turn")
                    if not isinstance(turn, dict):
                        turn = {}
                    status = str(turn.get("status") or params.get("status") or "")
                    state: CodingSessionState = {
                        "completed": "idle",
                        "interrupted": "interrupted",
                        "failed": "failed",
                    }.get(status, "unknown")
                    yield CodingSessionEvent(kind="completed", state=state)
                    return
                elif method in {"turn.failed", "error"}:
                    yield CodingSessionEvent(
                        kind="failed",
                        state="failed",
                        error_code=str(message.get("message") or method),
                    )
                    return
        finally:
            await self.close()

    async def interrupt(self) -> bool:
        try:
            await self._client.request(
                "turn/interrupt",
                {
                    "threadId": self._session_id,
                    "turnId": self.external_turn_id,
                },
            )
        except Exception:
            return False
        return True

    async def close(self) -> None:
        if not self._closed:
            self._closed = True
            await self._client.close()


class CodexAppServerProvider:
    """External provider over a Codex app-server sharing the desktop store."""

    provider_id = "codex"

    def __init__(
        self,
        *,
        command: Sequence[str] = ("codex", "app-server"),
        cwd: str = "",
        env: Mapping[str, str] | None = None,
        request_timeout_seconds: float = 30.0,
        client_factory: Callable[[], CodexAppServerClient] | None = None,
    ) -> None:
        self._command = tuple(command)
        self._cwd = cwd or None
        self._env = dict(env or {})
        self._request_timeout_seconds = request_timeout_seconds
        self._client_factory = client_factory or self._new_client
        self._active: dict[
            str, tuple[str, CodexAppServerClient, asyncio.Task[None]]
        ] = {}
        self._active_lock = asyncio.Lock()

    def _new_client(self) -> CodexAppServerClient:
        return CodexAppServerClient(
            self._command,
            cwd=self._cwd,
            env=self._env,
            request_timeout_seconds=self._request_timeout_seconds,
        )

    async def _request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        client = self._client_factory()
        await client.start()
        try:
            return await client.request(method, params)
        finally:
            await client.close()

    async def list_sessions(
        self,
        *,
        directory: str = "",
        search: str = "",
        limit: int = 50,
        cursor: str = "",
        include_archived: bool = False,
    ) -> CodingSessionPage:
        params: dict[str, Any] = {
            "limit": max(1, min(limit, 200)),
            "sortKey": "updated_at",
            "sortDirection": "desc",
            "archived": include_archived,
        }
        if directory:
            params["cwd"] = directory
        if search:
            params["searchTerm"] = search
        if cursor:
            params["cursor"] = cursor
        result = await self._request("thread/list", params)
        rows = result.get("data")
        threads = rows if isinstance(rows, list) else []
        return CodingSessionPage(
            sessions=tuple(
                self._summary(row) for row in threads if isinstance(row, dict)
            ),
            next_cursor=str(result.get("nextCursor") or ""),
        )

    async def get_session(self, external_session_id: str) -> CodingSessionDetail:
        try:
            read = await self._request(
                "thread/read",
                {"threadId": external_session_id, "includeTurns": False},
            )
            turns_result = await self._request(
                "thread/turns/list",
                {
                    "threadId": external_session_id,
                    "limit": 10,
                    "sortDirection": "desc",
                    "itemsView": "full",
                },
            )
        except Exception as exc:
            if "not found" in str(exc).lower():
                raise CodingSessionNotFound(external_session_id) from exc
            raise CodingSessionProviderError(str(exc)) from exc
        thread = read.get("thread")
        if not isinstance(thread, dict):
            raise CodingSessionNotFound(external_session_id)
        turns = turns_result.get("data")
        turn_rows = turns if isinstance(turns, list) else []
        messages = self._messages(turn_rows)
        summary = self._summary(thread, messages=messages, turns=turn_rows)
        return CodingSessionDetail(session=summary, messages=messages)

    async def continue_session(
        self, external_session_id: str, text: str
    ) -> CodingSessionTurnReceipt:
        return await self.start_turn(external_session_id, text)

    async def start_turn(
        self, external_session_id: str, text: str
    ) -> CodingSessionTurnReceipt:
        metadata = await self._request(
            "thread/read",
            {"threadId": external_session_id, "includeTurns": False},
        )
        thread = metadata.get("thread")
        status = thread.get("status") if isinstance(thread, dict) else None
        if not isinstance(status, dict):
            raise CodingSessionNotFound(external_session_id)
        if status.get("type") == "active":
            raise CodingSessionConflict("Codex thread is active in another client")
        client = self._client_factory()
        await client.start()
        try:
            await client.request(
                "thread/resume",
                {
                    "threadId": external_session_id,
                    "excludeTurns": True,
                },
            )
            result = await client.request(
                "turn/start",
                {
                    "threadId": external_session_id,
                    "input": [{"type": "text", "text": text}],
                },
            )
        except Exception as exc:
            await client.close()
            message = str(exc).lower()
            if "busy" in message or "active" in message or "conflict" in message:
                raise CodingSessionConflict(str(exc)) from exc
            raise CodingSessionProviderError(str(exc)) from exc
        turn = result.get("turn")
        turn_id = str(turn.get("id") or "") if isinstance(turn, dict) else ""
        if not turn_id:
            await client.close()
            raise CodingSessionProviderError("Codex turn/start returned no Turn ID")
        watcher = asyncio.create_task(
            self._watch_turn(turn_id, external_session_id, client),
            name=f"knoa-codex-external-{turn_id}",
        )
        async with self._active_lock:
            self._active[turn_id] = (external_session_id, client, watcher)
        return CodingSessionTurnReceipt(
            external_session_id=external_session_id,
            external_turn_id=turn_id,
            accepted=True,
        )

    async def _watch_turn(
        self,
        turn_id: str,
        external_session_id: str,
        client: CodexAppServerClient,
    ) -> None:
        try:
            async for message in client.events():
                method = str(message.get("method") or "")
                params = message.get("params")
                if not isinstance(params, dict):
                    params = {}
                event_turn = str(params.get("turnId") or "")
                if event_turn and event_turn != turn_id:
                    continue
                if method in {
                    "turn/completed",
                    "turn_complete",
                    "turn.failed",
                    "error",
                }:
                    return
        except asyncio.CancelledError:
            raise
        except Exception:
            return
        finally:
            async with self._active_lock:
                self._active.pop(turn_id, None)
            await client.close()

    async def interrupt_session(
        self, external_session_id: str, external_turn_id: str = ""
    ) -> bool:
        if not external_turn_id:
            async with self._active_lock:
                candidates = [
                    turn_id
                    for turn_id, (thread_id, _client, _task) in self._active.items()
                    if thread_id == external_session_id
                ]
            if candidates:
                external_turn_id = candidates[-1]
        if not external_turn_id:
            detail = await self.get_session(external_session_id)
            external_turn_id = detail.session.revision
        if not external_turn_id:
            return False
        async with self._active_lock:
            active = self._active.get(external_turn_id)
        if active is None:
            return False
        _thread_id, client, _task = active
        try:
            await client.request(
                "turn/interrupt",
                {"threadId": external_session_id, "turnId": external_turn_id},
            )
        except Exception:
            return False
        return True

    async def close(self) -> None:
        async with self._active_lock:
            active = tuple(self._active.values())
            self._active.clear()
        for _thread_id, client, task in active:
            task.cancel()
            await client.close()
        if active:
            await asyncio.gather(
                *(task for _thread_id, _client, task in active),
                return_exceptions=True,
            )

    def _summary(
        self,
        thread: dict[str, Any],
        *,
        messages: tuple[CodingSessionMessage, ...] = (),
        turns: Sequence[Any] = (),
    ) -> CodingSessionSummary:
        latest_user = next(
            (message.text for message in reversed(messages) if message.role == "user"),
            "",
        )
        latest_assistant = next(
            (
                message.text
                for message in reversed(messages)
                if message.role == "assistant"
            ),
            "",
        )
        status = thread.get("status")
        status_type = str(status.get("type") or "") if isinstance(status, dict) else ""
        # ``turns`` is newest-first: get_session requests thread/turns/list with
        # sortDirection=desc, so the latest turn is the first element.
        latest_turn = turns[0] if turns and isinstance(turns[0], dict) else None
        turn_status = str(latest_turn.get("status") or "") if latest_turn else ""
        if status_type == "active" or turn_status == "inProgress":
            state: CodingSessionState = "running"
        elif turn_status == "interrupted":
            state = "interrupted"
        elif turn_status == "failed" or status_type == "systemError":
            state = "failed"
        elif turn_status == "completed" or status_type == "idle":
            state = "idle"
        else:
            state = "unknown"
        revision = ""
        if latest_turn is not None:
            revision = str(latest_turn.get("id") or "")
        updated = thread.get("updatedAt")
        try:
            updated_at = float(updated or 0)
        except (TypeError, ValueError):
            updated_at = 0.0
        title = str(
            thread.get("name") or thread.get("preview") or "Untitled Codex thread"
        )
        external_session_id = str(thread.get("id") or "")
        if state != "running":
            capabilities: tuple[str, ...] = ("read", "continue")
        elif self._owns_live_turn(external_session_id):
            capabilities = ("read", "interrupt")
        else:
            capabilities = ("read",)
        return CodingSessionSummary(
            provider=self.provider_id,
            external_session_id=external_session_id,
            title=title,
            directory=str(thread.get("cwd") or ""),
            state=state,
            updated_at=updated_at,
            latest_user_message=latest_user,
            latest_assistant_message=latest_assistant,
            read_only=state == "running",
            capabilities=capabilities,
            native_state=status_type or turn_status,
            revision=revision,
        )

    def _owns_live_turn(self, external_session_id: str) -> bool:
        """Whether this provider holds the connection of a turn it started.

        ``turn/interrupt`` is answered by the app-server process that runs the
        turn, so a turn started by another client (the desktop app) cannot be
        interrupted from here.  Only the persisted turn status decides whether
        the session is running; the live handle just says who can stop it.
        """
        return any(
            thread_id == external_session_id
            for thread_id, _client, _task in self._active.values()
        )

    @staticmethod
    def _messages(turns: Sequence[Any]) -> tuple[CodingSessionMessage, ...]:
        messages: list[CodingSessionMessage] = []
        for turn in reversed(turns):
            if not isinstance(turn, dict):
                continue
            items = turn.get("items")
            if not isinstance(items, list):
                continue
            for item in items:
                if not isinstance(item, dict):
                    continue
                item_type = str(item.get("type") or "")
                if item_type == "userMessage":
                    parts = item.get("content")
                    text = (
                        " ".join(
                            str(part.get("text") or "")
                            for part in parts
                            if isinstance(part, dict) and part.get("type") == "text"
                        )
                        if isinstance(parts, list)
                        else ""
                    )
                    messages.append(
                        CodingSessionMessage(
                            message_id=str(item.get("id") or ""),
                            role="user",
                            text=text,
                            created_at=float(turn.get("startedAt") or 0),
                        )
                    )
                elif item_type == "agentMessage":
                    messages.append(
                        CodingSessionMessage(
                            message_id=str(item.get("id") or ""),
                            role="assistant",
                            text=str(item.get("text") or ""),
                            created_at=float(
                                turn.get("completedAt") or turn.get("startedAt") or 0
                            ),
                        )
                    )
        messages.sort(key=lambda item: item.created_at)
        return tuple(messages[-200:])


# A moving endpoint (service restart) retires its client instead of closing it,
# so requests still in flight on the previous service can finish.
_RETIRED_CLIENTS_KEPT = 2


class OpenCodeHttpProvider:
    """External provider over an existing OpenCode v2 HTTP service.

    The service is either given explicitly (``base_url`` + ``password``) or
    resolved on every request through a ``locator`` that follows the OpenCode
    background service of the current user.  With a locator "not running" is a
    normal state: listing yields nothing and session operations raise
    ``OpenCodeServiceUnavailable``.
    """

    provider_id = "opencode"

    def __init__(
        self,
        base_url: str | None = None,
        *,
        username: str = "opencode",
        password: str = "",
        directory: str = "",
        timeout_seconds: float = 30.0,
        client: httpx.AsyncClient | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        locator: OpenCodeEndpointSource | None = None,
    ) -> None:
        if locator is not None and (base_url is not None or client is not None):
            raise ValueError("A locator cannot be combined with an explicit endpoint")
        if locator is None and base_url is None and client is None:
            raise ValueError("OpenCodeHttpProvider needs a base_url or a locator")
        self._directory = directory
        self._timeout = timeout_seconds
        self._transport = transport
        self._locator = locator
        self._endpoint: OpenCodeEndpoint | None = None
        self._retired: list[httpx.AsyncClient] = []
        self._owns_client = client is None
        self._client: httpx.AsyncClient | None = client
        if client is None and locator is None and base_url is not None:
            self._client = self._new_client(base_url, username, password)

    def _new_client(
        self, base_url: str, username: str, password: str
    ) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            auth=(username, password),
            headers={"x-opencode-directory": self._directory} if self._directory else {},
            timeout=self._timeout,
            transport=self._transport,
        )

    async def _http(self) -> httpx.AsyncClient:
        """Return the client of the service that is current right now."""

        if self._locator is None:
            if self._client is None:
                raise CodingSessionProviderError("OpenCode client is closed")
            return self._client
        endpoint = self._locator.locate()
        if endpoint is None:
            raise OpenCodeServiceUnavailable(
                "OpenCode service is not running for this user"
            )
        if self._client is None or endpoint != self._endpoint:
            previous = self._client
            self._client = self._new_client(
                endpoint.base_url, endpoint.username, endpoint.password
            )
            self._endpoint = endpoint
            if previous is not None:
                self._retired.append(previous)
                while len(self._retired) > _RETIRED_CLIENTS_KEPT:
                    await self._retired.pop(0).aclose()
        return self._client

    async def _json(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        body: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        client = await self._http()
        try:
            response = await client.request(method, path, params=params, json=body)
        except httpx.ConnectError as exc:
            if self._locator is not None:
                # Nothing listens on the registered loopback port any more.
                raise OpenCodeServiceUnavailable(
                    "OpenCode service is not reachable"
                ) from exc
            raise CodingSessionProviderError(str(exc)) from exc
        except httpx.HTTPError as exc:
            raise CodingSessionProviderError(str(exc)) from exc
        if response.status_code == 404:
            raise CodingSessionNotFound(path)
        if response.status_code == 409:
            raise CodingSessionConflict(response.text)
        if response.status_code >= 400:
            raise CodingSessionProviderError(
                f"OpenCode {method} {path} returned {response.status_code}: {response.text[:500]}"
            )
        if response.status_code == 204 or not response.content:
            return {}
        value = response.json()
        return value if isinstance(value, dict) else {"data": value}

    async def list_sessions(
        self,
        *,
        directory: str = "",
        search: str = "",
        limit: int = 50,
        cursor: str = "",
        include_archived: bool = False,
    ) -> CodingSessionPage:
        params: dict[str, Any] = {
            "limit": max(1, min(limit, 200)),
            "order": "desc",
            "archived": "true" if include_archived else "false",
            "parentID": "null",
        }
        if directory or self._directory:
            params["directory"] = directory or self._directory
        if search:
            params["search"] = search
        if cursor:
            params["cursor"] = cursor
        try:
            listing, active = await asyncio.gather(
                self._json("GET", "/api/session", params=params),
                self._json("GET", "/api/session/active"),
            )
        except OpenCodeServiceUnavailable:
            # The user starts OpenCode on demand: "not running" is a normal
            # state, so there is simply nothing to list.
            return CodingSessionPage(sessions=(), next_cursor="")
        active_ids = active.get("data")
        active_set = set(active_ids) if isinstance(active_ids, dict) else set()
        rows = listing.get("data")
        sessions = tuple(
            self._summary(row, running=str(row.get("id") or "") in active_set)
            for row in (rows if isinstance(rows, list) else [])
            if isinstance(row, dict)
        )
        cursor_data = listing.get("cursor")
        next_cursor = (
            str(cursor_data.get("next") or "") if isinstance(cursor_data, dict) else ""
        )
        return CodingSessionPage(sessions=sessions, next_cursor=next_cursor)

    async def get_session(self, external_session_id: str) -> CodingSessionDetail:
        detail, messages, active = await asyncio.gather(
            self._json("GET", f"/api/session/{external_session_id}"),
            self._json(
                "GET",
                f"/api/session/{external_session_id}/message",
                params={"limit": 200, "order": "desc"},
            ),
            self._json("GET", "/api/session/active"),
        )
        row = detail.get("data")
        if not isinstance(row, dict):
            raise CodingSessionNotFound(external_session_id)
        active_ids = active.get("data")
        active_set = set(active_ids) if isinstance(active_ids, dict) else set()
        mapped = self._messages(messages.get("data"))
        summary = self._summary(
            row,
            running=external_session_id in active_set,
            messages=mapped,
        )
        return CodingSessionDetail(session=summary, messages=mapped)

    async def continue_session(
        self, external_session_id: str, text: str
    ) -> CodingSessionTurnReceipt:
        # No ``delivery`` mode on purpose: design.md only allows ``queue`` once
        # the provider reports that queueing is possible and the user chose it.
        # Busy sessions never reach this call (CodingSessionService keeps them
        # read-only) and OpenCode answers 409 for a conflicting admission.
        body = {"text": text, "resume": True}
        result = await self._json(
            "POST", f"/api/session/{external_session_id}/prompt", body=body
        )
        data = result.get("data")
        admitted = data if isinstance(data, dict) else {}
        return CodingSessionTurnReceipt(
            external_session_id=external_session_id,
            external_turn_id=str(
                admitted.get("id") or admitted.get("admittedSeq") or ""
            ),
            accepted=True,
        )

    async def interrupt_session(
        self, external_session_id: str, external_turn_id: str = ""
    ) -> bool:
        result = await self._json(
            "POST", f"/api/session/{external_session_id}/interrupt"
        )
        return bool(result.get("interrupted", True))

    async def stream_session(
        self, external_session_id: str
    ) -> AsyncIterator[CodingSessionEvent]:
        client = await self._http()
        async with client.stream("GET", "/api/event") as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if not line.startswith("data:"):
                    continue
                raw = line[5:].strip()
                if not raw or raw == "[DONE]":
                    continue
                try:
                    payload = httpx.Response(200, content=raw).json()
                except ValueError:
                    continue
                if not isinstance(payload, dict):
                    continue
                data = payload.get("data")
                if (
                    isinstance(data, dict)
                    and data.get("sessionID") != external_session_id
                ):
                    continue
                event_type = str(payload.get("type") or "")
                if event_type.endswith("text.delta") and isinstance(data, dict):
                    yield CodingSessionEvent(
                        kind="assistant_delta", text=str(data.get("text") or "")
                    )
                elif event_type == "session.idle":
                    yield CodingSessionEvent(kind="completed", state="idle")
                elif event_type == "session.error":
                    yield CodingSessionEvent(kind="failed", state="failed")

    async def close(self) -> None:
        clients = list(self._retired)
        self._retired = []
        if self._owns_client and self._client is not None:
            clients.append(self._client)
            self._client = None
            self._endpoint = None
        for client in clients:
            await client.aclose()

    def _summary(
        self,
        row: dict[str, Any],
        *,
        running: bool = False,
        messages: tuple[CodingSessionMessage, ...] = (),
    ) -> CodingSessionSummary:
        latest_user = next(
            (message.text for message in reversed(messages) if message.role == "user"),
            "",
        )
        latest_assistant = next(
            (
                message.text
                for message in reversed(messages)
                if message.role == "assistant"
            ),
            "",
        )
        outcome = str(row.get("outcome") or "")
        if running:
            state: CodingSessionState = "running"
        elif outcome in {"failed", "error"}:
            state = "failed"
        elif outcome == "interrupted":
            state = "interrupted"
        elif outcome:
            state = "idle"
        else:
            state = "unknown"
        time_data = row.get("time")
        updated_ms = time_data.get("updated") if isinstance(time_data, dict) else 0
        try:
            updated_at = float(updated_ms or 0) / 1000.0
        except (TypeError, ValueError):
            updated_at = 0.0
        location = row.get("location")
        directory = (
            str(location.get("directory") or "") if isinstance(location, dict) else ""
        )
        return CodingSessionSummary(
            provider=self.provider_id,
            external_session_id=str(row.get("id") or ""),
            title=str(row.get("title") or "Untitled OpenCode session"),
            directory=directory,
            state=state,
            updated_at=updated_at,
            latest_user_message=latest_user,
            latest_assistant_message=latest_assistant,
            read_only=running,
            # OpenCode runs every session inside the service we talk to
            # (``/api/session/active``), so a running session can be interrupted
            # no matter which client started it.
            capabilities=("read", "interrupt") if running else ("read", "continue"),
            native_state=outcome or ("running" if running else "unknown"),
            revision=str(updated_ms or ""),
        )

    @staticmethod
    def _messages(rows: Any) -> tuple[CodingSessionMessage, ...]:
        messages: list[CodingSessionMessage] = []
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, dict):
                continue
            kind = str(row.get("type") or "")
            text = ""
            if kind == "user":
                text = str(row.get("text") or "")
                role: Literal["user", "assistant", "system", "tool"] = "user"
            elif kind == "assistant":
                content = row.get("content")
                if isinstance(content, list):
                    text = "".join(
                        str(part.get("text") or "")
                        for part in content
                        if isinstance(part, dict) and part.get("type") == "text"
                    )
                role = "assistant"
            elif kind == "system":
                text = str(row.get("text") or "")
                role = "system"
            elif kind == "shell":
                text = str(row.get("output") or "")
                role = "tool"
            else:
                continue
            time_data = row.get("time")
            created_ms = time_data.get("created") if isinstance(time_data, dict) else 0
            try:
                created_at = float(created_ms or 0) / 1000.0
            except (TypeError, ValueError):
                created_at = 0.0
            messages.append(
                CodingSessionMessage(
                    message_id=str(row.get("id") or ""),
                    role=role,
                    text=text,
                    created_at=created_at,
                )
            )
        messages.sort(key=lambda item: item.created_at)
        return tuple(messages[-200:])
