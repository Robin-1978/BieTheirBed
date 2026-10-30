from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from knoa_codex_agent import (
    CodingSessionConflict,
    CodexAppServerProvider,
    OpenCodeHttpProvider,
)
from knoa_platform.coding_sessions import (
    CodingSessionBindingRepository,
    CodingSessionService,
)
from knoa_codex_agent.coding_sessions import (
    CodingSessionDetail,
    CodingSessionPage,
    CodingSessionSummary,
    CodingSessionTurnReceipt,
)


class FakeCodexClient:
    def __init__(self, status: str = "idle") -> None:
        self.status = status
        self.requests: list[tuple[str, dict]] = []
        self.closed = False
        self.queue: asyncio.Queue = asyncio.Queue()

    async def start(self) -> None:
        return None

    async def request(self, method: str, params: dict):
        self.requests.append((method, params))
        if method == "thread/list":
            return {
                "data": [
                    {
                        "id": "thread-a",
                        "name": "Fix search",
                        "cwd": "/work/project",
                        "preview": "please fix",
                        "updatedAt": 100,
                        "status": {"type": "idle"},
                    }
                ],
                "nextCursor": "next-a",
            }
        if method == "thread/read":
            return {
                "thread": {
                    "id": params["threadId"],
                    "name": "Fix search",
                    "cwd": "/work/project",
                    "preview": "please fix",
                    "updatedAt": 100,
                    "status": {"type": self.status},
                }
            }
        if method == "thread/turns/list":
            return {
                "data": [
                    {
                        "id": "turn-a",
                        "status": "completed",
                        "startedAt": 10,
                        "completedAt": 20,
                        "items": [
                            {
                                "id": "u1",
                                "type": "userMessage",
                                "content": [{"type": "text", "text": "please fix"}],
                            },
                            {
                                "id": "a1",
                                "type": "agentMessage",
                                "text": "fixed",
                            },
                        ],
                    }
                ]
            }
        if method == "thread/resume":
            return {"thread": {"id": params["threadId"]}}
        if method == "turn/start":
            return {"turn": {"id": "turn-b", "status": "inProgress"}}
        if method == "turn/interrupt":
            return {}
        raise AssertionError(method)

    async def events(self):
        item = await self.queue.get()
        if item is not None:
            yield item

    async def close(self) -> None:
        self.closed = True
        await self.queue.put(None)


class CodexFactory:
    def __init__(self, status: str = "idle") -> None:
        self.status = status
        self.clients: list[FakeCodexClient] = []

    def __call__(self):
        client = FakeCodexClient(self.status)
        self.clients.append(client)
        return client


@pytest.mark.asyncio
async def test_codex_provider_lists_and_reads_latest_messages() -> None:
    factory = CodexFactory()
    provider = CodexAppServerProvider(client_factory=factory)

    page = await provider.list_sessions(directory="/work/project", limit=20)

    assert page.next_cursor == "next-a"
    assert page.sessions[0].external_session_id == "thread-a"
    assert page.sessions[0].state == "idle"
    assert page.sessions[0].directory == "/work/project"
    assert factory.clients[0].requests[0][0] == "thread/list"

    detail = await provider.get_session("thread-a")

    assert detail.session.latest_user_message == "please fix"
    assert detail.session.latest_assistant_message == "fixed"
    assert [(m.role, m.text) for m in detail.messages] == [
        ("user", "please fix"),
        ("assistant", "fixed"),
    ]


@pytest.mark.asyncio
async def test_codex_provider_marks_active_thread_read_only() -> None:
    factory = CodexFactory(status="active")
    provider = CodexAppServerProvider(client_factory=factory)

    detail = await provider.get_session("thread-a")

    assert detail.session.state == "running"
    assert detail.session.read_only is True
    with pytest.raises(CodingSessionConflict):
        await provider.continue_session("thread-a", "keep going")


@pytest.mark.asyncio
async def test_codex_provider_keeps_connection_for_continued_turn() -> None:
    factory = CodexFactory()
    provider = CodexAppServerProvider(client_factory=factory)

    receipt = await provider.continue_session("thread-a", "keep going")
    client = factory.clients[-1]

    assert receipt.external_turn_id == "turn-b"
    assert [method for method, _params in client.requests] == [
        "thread/resume",
        "turn/start",
    ]
    assert factory.clients[0].requests[0][0] == "thread/read"
    assert client.closed is False
    assert await provider.interrupt_session("thread-a", "turn-b") is True
    await provider.close()
    assert client.closed is True


class _TurnsClient:
    """App-server stub whose ``thread/turns/list`` answers newest-first.

    The real ``codex app-server`` (codex-cli 0.165.0) returns strictly
    decreasing ``startedAt`` for ``sortDirection=desc``; the assertion below pins
    that request contract so the provider cannot silently switch to ascending.
    """

    def __init__(self, turns: list[dict], status: str = "idle") -> None:
        self._turns = turns
        self._status = status

    async def start(self) -> None:
        return None

    async def request(self, method: str, params: dict):
        if method == "thread/read":
            return {
                "thread": {
                    "id": params["threadId"],
                    "cwd": "/work/project",
                    "updatedAt": 100,
                    "status": {"type": self._status},
                }
            }
        if method == "thread/turns/list":
            assert params["sortDirection"] == "desc"
            return {"data": self._turns}
        raise AssertionError(method)

    async def close(self) -> None:
        return None


@pytest.mark.asyncio
async def test_codex_summary_reports_state_and_revision_of_the_newest_turn() -> None:
    turns = [
        {"id": "turn-newest", "status": "interrupted", "startedAt": 30, "items": []},
        {"id": "turn-middle", "status": "completed", "startedAt": 20, "items": []},
        {"id": "turn-oldest", "status": "completed", "startedAt": 10, "items": []},
    ]
    provider = CodexAppServerProvider(client_factory=lambda: _TurnsClient(turns))

    detail = await provider.get_session("thread-a")

    assert detail.session.state == "interrupted"
    assert detail.session.revision == "turn-newest"
    assert detail.session.read_only is False


@pytest.mark.asyncio
async def test_codex_summary_marks_in_progress_newest_turn_read_only() -> None:
    # A fresh app-server process reports every thread as ``notLoaded``, so the
    # persisted status of the newest turn is the only running signal available.
    turns = [
        {"id": "turn-live", "status": "inProgress", "startedAt": 30, "items": []},
        {"id": "turn-done", "status": "completed", "startedAt": 10, "items": []},
    ]
    provider = CodexAppServerProvider(
        client_factory=lambda: _TurnsClient(turns, status="notLoaded")
    )

    detail = await provider.get_session("thread-a")

    assert detail.session.state == "running"
    assert detail.session.read_only is True
    assert detail.session.capabilities == ("read",)


@pytest.mark.asyncio
async def test_opencode_provider_maps_sessions_messages_and_conflict() -> None:
    active = {"ses-a": {"type": "running"}}
    seen: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/api/session/active":
            return httpx.Response(200, json={"data": active})
        if request.url.path == "/api/session":
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "id": "ses-a",
                            "title": "Fix tests",
                            "location": {"directory": "/work/project"},
                            "outcome": "succeeded",
                            "time": {"updated": 1_700_000_000_000},
                        }
                    ],
                    "cursor": {"next": "next-page"},
                },
            )
        if request.url.path == "/api/session/ses-a":
            return httpx.Response(
                200,
                json={
                    "data": {
                        "id": "ses-a",
                        "title": "Fix tests",
                        "location": {"directory": "/work/project"},
                        "outcome": "succeeded",
                        "time": {"updated": 1_700_000_000_000},
                    }
                },
            )
        if request.url.path == "/api/session/ses-a/message":
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "id": "m1",
                            "type": "assistant",
                            "time": {"created": 1_700_000_000_000},
                            "content": [{"type": "text", "text": "all fixed"}],
                        },
                        {
                            "id": "m0",
                            "type": "user",
                            "text": "fix the tests",
                            "time": {"created": 1_699_999_999_000},
                        },
                    ]
                },
            )
        if request.url.path == "/api/session/ses-a/prompt":
            return httpx.Response(409, json={"_tag": "ConflictError"})
        raise AssertionError(request.url.path)

    client = httpx.AsyncClient(
        base_url="http://opencode",
        transport=httpx.MockTransport(handler),
    )
    provider = OpenCodeHttpProvider("http://opencode", password="secret", client=client)

    page = await provider.list_sessions(directory="/work/project")
    detail = await provider.get_session("ses-a")

    assert page.next_cursor == "next-page"
    assert page.sessions[0].state == "running"
    assert page.sessions[0].read_only is True
    assert detail.messages[-1].text == "all fixed"
    assert detail.session.updated_at == 1_700_000_000.0
    with pytest.raises(CodingSessionConflict):
        await provider.continue_session("ses-a", "continue")

    await client.aclose()


class _ServiceProvider:
    provider_id = "fake"

    def __init__(self) -> None:
        self.prompts: list[str] = []

    async def list_sessions(self, **_kwargs):
        return CodingSessionPage(
            sessions=(
                CodingSessionSummary(
                    provider="fake",
                    external_session_id="external-a",
                    title="A",
                    directory="/work/project",
                    state="idle",
                    updated_at=3,
                ),
            )
        )

    async def get_session(self, external_session_id):
        return CodingSessionDetail(
            session=CodingSessionSummary(
                provider="fake",
                external_session_id=external_session_id,
                title="A",
                directory="/work/project",
                state="idle",
                updated_at=3,
            )
        )

    async def continue_session(self, external_session_id, text):
        self.prompts.append(text)
        return CodingSessionTurnReceipt(external_session_id, "turn-a", True)

    async def interrupt_session(self, _external_session_id, _external_turn_id=""):
        return True

    async def close(self):
        return None


@pytest.mark.asyncio
async def test_coding_session_service_requires_binding_and_enforces_directory(
    tmp_path,
) -> None:
    provider = _ServiceProvider()
    service = CodingSessionService(
        {"fake": provider},
        CodingSessionBindingRepository(tmp_path / "bindings.db"),
        allowed_directories=("/work",),
    )

    listed = await service.list_sessions(provider="fake")
    assert listed.sessions[0].external_session_id == "external-a"
    with pytest.raises(LookupError):
        await service.continue_session(
            principal_id="principal-a",
            provider="fake",
            external_session_id="external-a",
            text="continue",
        )

    binding = await service.attach(
        principal_id="principal-a",
        session_handle="knoa-session-a",
        provider="fake",
        external_session_id="external-a",
    )
    assert binding.session_handle == "knoa-session-a"
    receipt = await service.continue_session(
        principal_id="principal-a",
        provider="fake",
        external_session_id="external-a",
        text="continue",
    )
    assert receipt.accepted is True
    assert provider.prompts == ["continue"]
    with pytest.raises(PermissionError):
        await service.list_sessions(directory="/not-allowed")


def _attach(repository, *, principal="principal-a", handle="knoa-a", external="ext-a"):
    return repository.attach(
        principal_id=principal,
        session_handle=handle,
        provider="fake",
        external_session_id=external,
        directory="/work/project",
    )


def test_binding_rejects_external_session_bound_to_another_conversation(
    tmp_path,
) -> None:
    repository = CodingSessionBindingRepository(tmp_path / "bindings.db")
    first = _attach(repository, handle="knoa-a")

    with pytest.raises(CodingSessionConflict):
        _attach(repository, handle="knoa-b")

    # The original owner keeps the binding and the rejected conversation stays unbound.
    assert repository.get_external("principal-a", "fake", "ext-a") == first
    with pytest.raises(LookupError):
        repository.get_by_session("principal-a", "knoa-b")


def test_binding_rejects_rebinding_a_conversation_to_another_external_session(
    tmp_path,
) -> None:
    repository = CodingSessionBindingRepository(tmp_path / "bindings.db")
    _attach(repository, external="ext-a")

    with pytest.raises(CodingSessionConflict):
        _attach(repository, external="ext-b")


def test_binding_attach_is_idempotent_for_the_same_conversation(tmp_path) -> None:
    repository = CodingSessionBindingRepository(tmp_path / "bindings.db")
    first = _attach(repository)

    again = _attach(repository)

    assert again.binding_id == first.binding_id
    assert again.session_handle == "knoa-a"


def test_binding_uniqueness_is_scoped_to_the_principal(tmp_path) -> None:
    repository = CodingSessionBindingRepository(tmp_path / "bindings.db")
    first = _attach(repository, principal="principal-a", handle="knoa-a")

    second = _attach(repository, principal="principal-b", handle="knoa-b")

    assert second.binding_id != first.binding_id
    assert second.principal_id == "principal-b"


class _RunningTurnClient(FakeCodexClient):
    """``FakeCodexClient`` whose newest persisted turn is still in progress."""

    async def request(self, method: str, params: dict):
        if method == "thread/turns/list":
            self.requests.append((method, params))
            return {
                "data": [
                    {
                        "id": "turn-b",
                        "status": "inProgress",
                        "startedAt": 30,
                        "items": [],
                    }
                ]
            }
        return await super().request(method, params)


@pytest.mark.asyncio
async def test_codex_declares_interrupt_only_for_a_turn_it_started() -> None:
    provider = CodexAppServerProvider(client_factory=_RunningTurnClient)
    await provider.continue_session("thread-a", "keep going")

    own = await provider.get_session("thread-a")
    foreign = await provider.get_session("thread-desktop")

    # Both threads report a running newest turn, but only ``thread-a`` has a live
    # app-server connection in this provider, which is what turn/interrupt needs.
    assert own.session.state == "running"
    assert own.session.capabilities == ("read", "interrupt")
    assert foreign.session.state == "running"
    assert foreign.session.capabilities == ("read",)
    await provider.close()


@pytest.mark.asyncio
async def test_codex_idle_session_declares_continue_but_not_interrupt() -> None:
    provider = CodexAppServerProvider(client_factory=CodexFactory())

    detail = await provider.get_session("thread-a")

    assert detail.session.state == "idle"
    assert detail.session.capabilities == ("read", "continue")


@pytest.mark.asyncio
async def test_codex_live_handle_alone_does_not_declare_interrupt() -> None:
    # The persisted newest turn is already completed while the watcher still
    # holds the connection: the persisted state wins, so nothing is interruptible.
    provider = CodexAppServerProvider(client_factory=CodexFactory())
    await provider.continue_session("thread-a", "keep going")

    detail = await provider.get_session("thread-a")

    assert detail.session.state == "idle"
    assert detail.session.capabilities == ("read", "continue")
    await provider.close()


def _opencode(handler):
    client = httpx.AsyncClient(
        base_url="http://opencode", transport=httpx.MockTransport(handler)
    )
    provider = OpenCodeHttpProvider("http://opencode", password="secret", client=client)
    return provider, client


@pytest.mark.asyncio
async def test_opencode_declares_interrupt_only_while_running() -> None:
    active: dict = {"ses-a": {"type": "running"}}

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/session/active":
            return httpx.Response(200, json={"data": active})
        if request.url.path == "/api/session/ses-a":
            return httpx.Response(
                200,
                json={
                    "data": {
                        "id": "ses-a",
                        "title": "Fix tests",
                        "location": {"directory": "/work/project"},
                        "outcome": "succeeded",
                        "time": {"updated": 1_700_000_000_000},
                    }
                },
            )
        if request.url.path == "/api/session/ses-a/message":
            return httpx.Response(200, json={"data": []})
        raise AssertionError(request.url.path)

    provider, client = _opencode(handler)
    running = await provider.get_session("ses-a")
    active.clear()
    idle = await provider.get_session("ses-a")

    assert running.session.capabilities == ("read", "interrupt")
    assert idle.session.capabilities == ("read", "continue")
    await client.aclose()


@pytest.mark.asyncio
async def test_opencode_continue_does_not_force_queued_delivery() -> None:
    bodies: list[dict] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/session/ses-a/prompt":
            bodies.append(json.loads(request.content))
            return httpx.Response(200, json={"data": {"id": "inbox-1"}})
        raise AssertionError(request.url.path)

    provider, client = _opencode(handler)
    receipt = await provider.continue_session("ses-a", "go on")

    # design.md: ``delivery=queue`` is only used once the provider reports that
    # queueing is possible and the user chose it, so the default prompt carries
    # no delivery mode and OpenCode applies its own default.
    assert bodies == [{"text": "go on", "resume": True}]
    assert receipt.external_turn_id == "inbox-1"
    assert receipt.accepted is True
    await client.aclose()


class _InterruptProvider(_ServiceProvider):
    """Reports a configurable capability set so the service gate can be exercised."""

    def __init__(self, capabilities: tuple[str, ...]) -> None:
        super().__init__()
        self.capabilities = capabilities
        self.interrupts: list[str] = []

    async def get_session(self, external_session_id):
        return CodingSessionDetail(
            session=CodingSessionSummary(
                provider="fake",
                external_session_id=external_session_id,
                title="A",
                directory="/work/project",
                state="running",
                updated_at=3,
                read_only=True,
                capabilities=self.capabilities,
            )
        )

    async def interrupt_session(self, external_session_id, external_turn_id=""):
        self.interrupts.append(external_session_id)
        return True


async def _bound_service(tmp_path, provider):
    service = CodingSessionService(
        {"fake": provider},
        CodingSessionBindingRepository(tmp_path / "bindings.db"),
    )
    await service.attach(
        principal_id="principal-a",
        session_handle="knoa-a",
        provider="fake",
        external_session_id="external-a",
    )
    return service


@pytest.mark.asyncio
async def test_service_interrupts_a_session_that_declares_interrupt(tmp_path) -> None:
    provider = _InterruptProvider(("read", "interrupt"))
    service = await _bound_service(tmp_path, provider)

    interrupted = await service.interrupt_session(
        principal_id="principal-a",
        provider="fake",
        external_session_id="external-a",
    )

    assert interrupted is True
    assert provider.interrupts == ["external-a"]


@pytest.mark.asyncio
async def test_service_refuses_to_interrupt_without_the_capability(tmp_path) -> None:
    provider = _InterruptProvider(("read",))
    service = await _bound_service(tmp_path, provider)

    with pytest.raises(CodingSessionConflict):
        await service.interrupt_session(
            principal_id="principal-a",
            provider="fake",
            external_session_id="external-a",
        )

    assert provider.interrupts == []
