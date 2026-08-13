from __future__ import annotations

import socket

import pytest

from knoa_platform.tools.web_fetch import WebFetchTool, _is_safe_url


def test_safe_url_fails_closed_when_dns_resolution_fails(monkeypatch) -> None:
    def fail_resolution(*_args):
        raise socket.gaierror("unavailable")

    monkeypatch.setattr(socket, "getaddrinfo", fail_resolution)

    safe, reason = _is_safe_url("https://example.com/report")

    assert not safe
    assert reason == "Hostname resolution failed"


@pytest.mark.parametrize(
    "address",
    ["127.0.0.1", "169.254.1.1", "10.0.0.1", "::1", "224.0.0.1"],
)
def test_safe_url_rejects_every_non_global_address(monkeypatch, address: str) -> None:
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *_args: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 0))],
    )

    safe, reason = _is_safe_url("https://example.com/report")

    assert not safe
    assert "non-global IP" in reason


@pytest.mark.asyncio
async def test_redirect_target_is_revalidated_before_second_request(
    monkeypatch,
) -> None:
    requested = []
    validated = []

    class Response:
        is_redirect = True
        headers = {"location": "http://127.0.0.1/private"}

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

    class Client:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        def stream(self, method, url):
            assert method == "GET"
            requested.append(url)
            return Response()

    def validate(url):
        validated.append(url)
        if url.startswith("http://127.0.0.1"):
            return False, "Access to non-global IP is blocked: 127.0.0.1"
        return True, ""

    monkeypatch.setattr("httpx.AsyncClient", Client)
    monkeypatch.setattr("knoa_platform.tools.web_fetch._is_safe_url", validate)

    result = await WebFetchTool().execute(url="https://example.com/start")

    assert requested == ["https://example.com/start"]
    assert validated == [
        "https://example.com/start",
        "http://127.0.0.1/private",
    ]
    assert "URL blocked" in result["error"]


@pytest.mark.asyncio
async def test_response_body_is_rejected_when_declared_size_exceeds_limit(
    monkeypatch,
) -> None:
    class Response:
        is_redirect = False
        headers = {"content-length": "6"}
        encoding = "utf-8"
        status_code = 200

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        def raise_for_status(self):
            return None

        async def aiter_bytes(self):
            raise AssertionError("oversized declared body must not be read")
            yield b""

    class Client:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        def stream(self, method, url):
            assert method == "GET"
            assert url == "https://example.com/report"
            return Response()

    monkeypatch.setattr("httpx.AsyncClient", Client)
    monkeypatch.setattr(
        "knoa_platform.tools.web_fetch._is_safe_url",
        lambda _url: (True, ""),
    )
    monkeypatch.setattr("knoa_platform.tools.web_fetch._MAX_RESPONSE_BYTES", 5)

    result = await WebFetchTool().execute(url="https://example.com/report")

    assert result == {"error": "HTTP response exceeds 5 byte limit"}


@pytest.mark.asyncio
async def test_chunked_response_body_is_stopped_at_hard_limit(monkeypatch) -> None:
    chunks_read = []

    class Response:
        is_redirect = False
        headers = {}
        encoding = "utf-8"
        status_code = 200

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        def raise_for_status(self):
            return None

        async def aiter_bytes(self):
            for chunk in (b"123", b"456", b"unread"):
                chunks_read.append(chunk)
                yield chunk

    class Client:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        def stream(self, _method, _url):
            return Response()

    monkeypatch.setattr("httpx.AsyncClient", Client)
    monkeypatch.setattr(
        "knoa_platform.tools.web_fetch._is_safe_url",
        lambda _url: (True, ""),
    )
    monkeypatch.setattr("knoa_platform.tools.web_fetch._MAX_RESPONSE_BYTES", 5)

    result = await WebFetchTool().execute(url="https://example.com/report")

    assert result == {"error": "HTTP response exceeds 5 byte limit"}
    assert chunks_read == [b"123", b"456"]
