"""KNOA follows the OpenCode background service of the current user.

Without an explicit URL/password the OpenCode provider asks a locator for the
service registration on every request, so a restarted service (new port, pid
or password) is picked up without restarting KNOA.
"""

from __future__ import annotations

import base64
from types import SimpleNamespace

import httpx
import pytest
from pydantic import SecretStr

from knoa_codex_agent import (
    CodingSessionPage,
    CodingSessionProviderError,
    CodingSessionSummary,
    OpenCodeEndpoint,
    OpenCodeHttpProvider,
    OpenCodeServiceUnavailable,
)
from knoa_platform.coding_sessions import build_coding_session_service
from knoa_platform.config import load_config
from knoa_platform.opencode_service import (
    OpenCodeServiceLocator,
    OpenCodeServiceRecordError,
)


class _Locator:
    """Stands in for OpenCodeServiceLocator; ``endpoint`` changes like on a restart."""

    def __init__(self, endpoint: OpenCodeEndpoint | None) -> None:
        self.endpoint = endpoint

    def locate(self) -> OpenCodeEndpoint | None:
        return self.endpoint


def _endpoint(port: int = 50000, password: str = "pw") -> OpenCodeEndpoint:
    return OpenCodeEndpoint(f"http://127.0.0.1:{port}", password, pid=port)


def _basic(password: str) -> str:
    return "Basic " + base64.b64encode(f"opencode:{password}".encode()).decode()


def _listing_handler(seen: list[tuple[int | None, str | None, str | None]]):
    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(
            (
                request.url.port,
                request.headers.get("authorization"),
                request.headers.get("x-opencode-directory"),
            )
        )
        if request.url.path == "/api/session":
            return httpx.Response(200, json={"data": [], "cursor": {}})
        if request.url.path == "/api/session/active":
            return httpx.Response(200, json={"data": {}})
        raise AssertionError(request.url.path)

    return handler


async def _no_request(request: httpx.Request) -> httpx.Response:
    raise AssertionError("nothing may be sent while the service is not running")


async def _refuse(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("connection refused", request=request)


@pytest.mark.asyncio
async def test_discovery_follows_the_service_across_a_restart() -> None:
    seen: list[tuple[int | None, str | None, str | None]] = []
    locator = _Locator(_endpoint(50000, "pw-old"))
    provider = OpenCodeHttpProvider(
        locator=locator, transport=httpx.MockTransport(_listing_handler(seen))
    )

    await provider.list_sessions()
    locator.endpoint = _endpoint(50001, "pw-new")
    await provider.list_sessions()
    await provider.close()

    before, after = seen[:2], seen[2:]
    assert {(port, auth) for port, auth, _ in before} == {(50000, _basic("pw-old"))}
    assert {(port, auth) for port, auth, _ in after} == {(50001, _basic("pw-new"))}


@pytest.mark.asyncio
async def test_discovery_keeps_the_configured_directory_header() -> None:
    seen: list[tuple[int | None, str | None, str | None]] = []
    provider = OpenCodeHttpProvider(
        locator=_Locator(_endpoint()),
        directory="/work/project",
        transport=httpx.MockTransport(_listing_handler(seen)),
    )

    await provider.list_sessions()
    await provider.close()

    assert {directory for _port, _auth, directory in seen} == {"/work/project"}


@pytest.mark.asyncio
async def test_discovery_lists_nothing_while_the_service_is_not_running() -> None:
    provider = OpenCodeHttpProvider(
        locator=_Locator(None), transport=httpx.MockTransport(_no_request)
    )

    page = await provider.list_sessions()

    assert page.sessions == ()
    assert page.next_cursor == ""
    await provider.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["get", "continue", "interrupt"])
async def test_discovery_fails_clearly_for_a_session_while_the_service_is_not_running(
    operation: str,
) -> None:
    provider = OpenCodeHttpProvider(
        locator=_Locator(None), transport=httpx.MockTransport(_no_request)
    )
    calls = {
        "get": lambda: provider.get_session("ses-a"),
        "continue": lambda: provider.continue_session("ses-a", "go on"),
        "interrupt": lambda: provider.interrupt_session("ses-a"),
    }

    with pytest.raises(OpenCodeServiceUnavailable):
        await calls[operation]()

    # A provider error is what the gateway answers with 503.
    assert issubclass(OpenCodeServiceUnavailable, CodingSessionProviderError)
    await provider.close()


@pytest.mark.asyncio
async def test_discovery_treats_connection_refused_as_not_running() -> None:
    provider = OpenCodeHttpProvider(
        locator=_Locator(_endpoint()), transport=httpx.MockTransport(_refuse)
    )

    page = await provider.list_sessions()
    with pytest.raises(OpenCodeServiceUnavailable):
        await provider.get_session("ses-a")

    assert page.sessions == ()
    await provider.close()


@pytest.mark.asyncio
async def test_an_explicit_endpoint_still_reports_connection_errors() -> None:
    provider = OpenCodeHttpProvider(
        "http://opencode", password="secret", transport=httpx.MockTransport(_refuse)
    )

    with pytest.raises(CodingSessionProviderError) as info:
        await provider.list_sessions()

    assert not isinstance(info.value, OpenCodeServiceUnavailable)
    await provider.close()


@pytest.mark.asyncio
async def test_discovery_surfaces_registration_problems_instead_of_hiding_them() -> None:
    class _Broken:
        def locate(self) -> OpenCodeEndpoint | None:
            raise OpenCodeServiceRecordError("OpenCode registration is not private")

    provider = OpenCodeHttpProvider(
        locator=_Broken(), transport=httpx.MockTransport(_no_request)
    )

    with pytest.raises(CodingSessionProviderError, match="not private"):
        await provider.list_sessions()
    await provider.close()


@pytest.mark.asyncio
async def test_provider_closes_every_client_it_created_for_a_moving_endpoint() -> None:
    closed: list[int] = []

    class _CountingTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            if request.url.path == "/api/session/active":
                return httpx.Response(200, json={"data": {}})
            return httpx.Response(200, json={"data": [], "cursor": {}})

        async def aclose(self) -> None:
            closed.append(1)

    locator = _Locator(_endpoint(50000))
    provider = OpenCodeHttpProvider(locator=locator, transport=_CountingTransport())

    await provider.list_sessions()
    await provider.list_sessions()
    locator.endpoint = _endpoint(50001)
    await provider.list_sessions()
    await provider.close()

    assert len(closed) == 2


def test_provider_needs_exactly_one_connection_source() -> None:
    with pytest.raises(ValueError):
        OpenCodeHttpProvider()
    with pytest.raises(ValueError):
        OpenCodeHttpProvider(
            "http://opencode", password="secret", locator=_Locator(None)
        )


class _RecordingProvider:
    """Captures how the builder constructs the OpenCode provider."""

    provider_id = "opencode"
    instances: list[_RecordingProvider] = []

    def __init__(self, *args: object, **kwargs: object) -> None:
        self.args = args
        self.kwargs = kwargs
        type(self).instances.append(self)

    async def close(self) -> None:
        return None


def _config(**overrides: object) -> SimpleNamespace:
    values: dict[str, object] = {
        "coding_sessions_enabled": True,
        "codex_app_server_command": ("codex", "app-server"),
        "codex_app_server_cwd": "",
        "opencode_server_url": "",
        "opencode_server_username": "opencode",
        "opencode_server_password": SecretStr(""),
        "opencode_server_directory": "",
        "opencode_service_discovery": True,
        "coding_session_allowed_directories": (),
        "working_directory": "",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


@pytest.fixture
def recorded(monkeypatch: pytest.MonkeyPatch) -> list[_RecordingProvider]:
    _RecordingProvider.instances = []
    monkeypatch.setattr(
        "knoa_platform.coding_sessions.OpenCodeHttpProvider", _RecordingProvider
    )
    return _RecordingProvider.instances


def test_builder_follows_the_users_opencode_service_by_default(
    tmp_path, recorded: list[_RecordingProvider]
) -> None:
    service = build_coding_session_service(_config(), tmp_path / "bindings.db")

    assert service.provider_ids() == ("codex", "opencode")
    (opencode,) = recorded
    assert opencode.args == ()
    assert isinstance(opencode.kwargs["locator"], OpenCodeServiceLocator)
    assert "password" not in opencode.kwargs


def test_builder_prefers_an_explicit_endpoint(
    tmp_path, recorded: list[_RecordingProvider]
) -> None:
    config = _config(
        opencode_server_url="http://127.0.0.1:4096",
        opencode_server_password=SecretStr("pw"),
    )

    service = build_coding_session_service(config, tmp_path / "bindings.db")

    assert service.provider_ids() == ("codex", "opencode")
    (opencode,) = recorded
    assert opencode.args == ("http://127.0.0.1:4096",)
    assert opencode.kwargs["password"] == "pw"
    assert "locator" not in opencode.kwargs


def test_builder_skips_opencode_when_discovery_is_switched_off(
    tmp_path, recorded: list[_RecordingProvider]
) -> None:
    config = _config(opencode_service_discovery=False)

    service = build_coding_session_service(config, tmp_path / "bindings.db")

    assert service.provider_ids() == ("codex",)
    assert recorded == []


def test_builder_does_not_replace_an_incomplete_explicit_endpoint(
    tmp_path, recorded: list[_RecordingProvider]
) -> None:
    config = _config(opencode_server_url="http://127.0.0.1:4096")

    service = build_coding_session_service(config, tmp_path / "bindings.db")

    assert service.provider_ids() == ("codex",)
    assert recorded == []


def test_builder_hands_the_configured_directory_to_the_provider(
    tmp_path, recorded: list[_RecordingProvider]
) -> None:
    config = _config(opencode_server_directory="/work/project")

    build_coding_session_service(config, tmp_path / "bindings.db")

    (opencode,) = recorded
    assert opencode.kwargs["directory"] == "/work/project"


def test_builder_registers_nothing_when_coding_sessions_are_disabled(
    tmp_path, recorded: list[_RecordingProvider]
) -> None:
    config = _config(coding_sessions_enabled=False)

    service = build_coding_session_service(config, tmp_path / "bindings.db")

    assert service.provider_ids() == ()
    assert recorded == []


class _FakeCodex:
    """Offers one session in a directory outside KNOA's own workspace."""

    provider_id = "codex"

    def __init__(self, *args: object, **kwargs: object) -> None:
        pass

    async def list_sessions(self, **kwargs: object) -> CodingSessionPage:
        return CodingSessionPage(
            sessions=(
                CodingSessionSummary(
                    provider="codex",
                    external_session_id="thr-1",
                    title="Elsewhere",
                    directory="/disk/dev/other/project",
                    state="idle",
                    updated_at=1.0,
                    capabilities=("read", "continue"),
                ),
            )
        )

    async def close(self) -> None:
        return None


@pytest.mark.asyncio
async def test_builder_shows_every_session_of_the_user_unless_directories_are_listed(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("knoa_platform.coding_sessions.CodexAppServerProvider", _FakeCodex)
    workspace = "/home/user/.local/share/knoa/workspace"
    everything = build_coding_session_service(
        _config(opencode_service_discovery=False, working_directory=workspace),
        tmp_path / "all.db",
    )
    narrowed = build_coding_session_service(
        _config(
            opencode_service_discovery=False,
            working_directory=workspace,
            coding_session_allowed_directories=("/home/user/ws",),
        ),
        tmp_path / "narrow.db",
    )

    visible = await everything.list_sessions()
    restricted = await narrowed.list_sessions()

    assert [item.external_session_id for item in visible.sessions] == ["thr-1"]
    assert restricted.sessions == ()


def test_discovery_switch_defaults_on_and_follows_the_environment(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    monkeypatch.setenv("KNOA_HOME", str(tmp_path / ".knoa"))
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("KNOA_OPENCODE_SERVICE_DISCOVERY", raising=False)
    assert load_config().opencode_service_discovery is True

    monkeypatch.setenv("KNOA_OPENCODE_SERVICE_DISCOVERY", "false")
    assert load_config().opencode_service_discovery is False
