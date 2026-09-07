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


@pytest.mark.asyncio
async def test_web_fetch_extracts_clean_text_and_focuses_on_query(monkeypatch) -> None:
    html_content = """
    <html>
      <head><title>Test Page</title></head>
      <body>
        <nav><a href="/home">Home</a><a href="/faq">FAQ</a></nav>
        <div class="ad-banner">Annoying Ad Banner</div>
        <main>
          <h1>Overview</h1>
          <p>Introductory paragraph with generic information.</p>
          <h2>Detailed Benchmark</h2>
          <p>Astra Code Arena benchmark score is 1797, achieving new state of the art.</p>
          <p>Safety considerations: deep thought loops need oversight.</p>
        </main>
        <footer>Copyright 2026</footer>
      </body>
    </html>
    """

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
            yield html_content.encode("utf-8")

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

    tool = WebFetchTool()
    res = await tool.execute(url="https://example.com/test", query="Code Arena 1797")

    assert res["status_code"] == 200
    assert "1797" in res["content"]
    assert "Home" not in res["content"]
    assert "Annoying Ad Banner" not in res["content"]
    assert "Copyright 2026" not in res["content"]


@pytest.mark.asyncio
async def test_web_fetch_definition_includes_query() -> None:
    tool = WebFetchTool()
    definition = tool.definition()
    assert "query" in definition["inputSchema"]["properties"]
    assert definition["inputSchema"]["required"] == ["url"]


@pytest.mark.asyncio
async def test_web_fetch_retries_on_403_with_fallback_headers(monkeypatch) -> None:
    attempts = []

    class ResponseSuccess:
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
            yield b"<html><body><p>Recovered content from fallback attempt</p></body></html>"

    class ResponseForbidden:
        is_redirect = False
        headers = {}
        encoding = "utf-8"
        status_code = 403

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        def raise_for_status(self):
            import httpx
            raise httpx.HTTPStatusError("403 Forbidden", request=None, response=self)

        async def aiter_bytes(self):
            yield b"Forbidden"

    class MockClient:
        def __init__(self, headers=None, **_kwargs):
            self.headers = headers or {}
            attempts.append(self.headers)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        def stream(self, _method, _url):
            if len(attempts) == 1:
                return ResponseForbidden()
            return ResponseSuccess()

    monkeypatch.setattr("httpx.AsyncClient", MockClient)
    monkeypatch.setattr(
        "knoa_platform.tools.web_fetch._is_safe_url",
        lambda _url: (True, ""),
    )

    tool = WebFetchTool()
    res = await tool.execute(url="https://example.com/protected")

    assert len(attempts) == 2
    assert "Chrome" in attempts[0].get("User-Agent", "")
    assert "Safari" in attempts[1].get("User-Agent", "")
    assert res["status_code"] == 200
    assert "Recovered content" in res["content"]


@pytest.mark.asyncio
async def test_web_fetch_returns_friendly_error_when_403_persists(monkeypatch) -> None:
    class ResponseForbidden:
        is_redirect = False
        headers = {}
        encoding = "utf-8"
        status_code = 403

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        def raise_for_status(self):
            import httpx
            raise httpx.HTTPStatusError("403 Forbidden", request=None, response=self)

        async def aiter_bytes(self):
            yield b"Forbidden"

    class MockClient:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        def stream(self, _method, _url):
            return ResponseForbidden()

    monkeypatch.setattr("httpx.AsyncClient", MockClient)
    monkeypatch.setattr(
        "knoa_platform.tools.web_fetch._is_safe_url",
        lambda _url: (True, ""),
    )

    tool = WebFetchTool()
    res = await tool.execute(url="https://example.com/anti-spider")

    assert "error" in res
    assert "403" in res["error"]
    assert "anti-bot" in res["error"] or "JavaScript challenge" in res["error"]


@pytest.mark.asyncio
async def test_web_fetch_does_not_retry_on_empty_200_response(monkeypatch) -> None:
    attempts = 0

    class ResponseEmpty:
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
            if False:
                yield b""

    class MockClient:
        def __init__(self, **_kwargs):
            nonlocal attempts
            attempts += 1

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        def stream(self, _method, _url):
            return ResponseEmpty()

    monkeypatch.setattr("httpx.AsyncClient", MockClient)
    monkeypatch.setattr(
        "knoa_platform.tools.web_fetch._is_safe_url",
        lambda _url: (True, ""),
    )

    tool = WebFetchTool()
    res = await tool.execute(url="https://example.com/empty-page")

    assert attempts == 1
    assert res["status_code"] == 200
    assert res["content"] == ""



