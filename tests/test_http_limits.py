from __future__ import annotations

import pytest

from pc_assistant.tools.http_limits import (
    HttpResponseTooLargeError,
    iter_limited_lines,
    read_limited_bytes,
    read_limited_json,
)


class Response:
    def __init__(self, chunks, *, content_length: str | None = None) -> None:
        self._chunks = chunks
        self.headers = {}
        if content_length is not None:
            self.headers["content-length"] = content_length
        self.encoding = "utf-8"

    async def aiter_bytes(self):
        for chunk in self._chunks:
            yield chunk


@pytest.mark.asyncio
async def test_declared_oversize_is_rejected_before_stream_read() -> None:
    response = Response((b"must-not-be-read",), content_length="6")

    with pytest.raises(HttpResponseTooLargeError, match="5 byte limit"):
        await read_limited_bytes(response, 5)


@pytest.mark.asyncio
async def test_chunked_oversize_is_rejected_at_hard_limit() -> None:
    response = Response((b"123", b"456"))

    with pytest.raises(HttpResponseTooLargeError, match="5 byte limit"):
        await read_limited_bytes(response, 5)


@pytest.mark.asyncio
async def test_bounded_json_response_is_decoded() -> None:
    response = Response((b'{"ok":', b"true}"))

    assert await read_limited_json(response, 32) == {"ok": True}


@pytest.mark.asyncio
async def test_stream_lines_are_incremental_and_crlf_normalized() -> None:
    response = Response((b"one\r\npar", b"tial\ntwo"))

    lines = [
        line
        async for line in iter_limited_lines(
            response,
            max_line_bytes=16,
            max_total_bytes=32,
        )
    ]

    assert lines == ["one", "partial", "two"]


@pytest.mark.asyncio
async def test_stream_rejects_line_without_bounded_delimiter() -> None:
    response = Response((b"123", b"456"))

    with pytest.raises(HttpResponseTooLargeError, match="stream line"):
        async for _line in iter_limited_lines(
            response,
            max_line_bytes=5,
            max_total_bytes=10,
        ):
            pass


@pytest.mark.asyncio
async def test_stream_rejects_excessive_total_bytes() -> None:
    response = Response((b"1\n", b"2\n", b"3\n"))

    with pytest.raises(HttpResponseTooLargeError, match="stream exceeds"):
        async for _line in iter_limited_lines(
            response,
            max_line_bytes=5,
            max_total_bytes=5,
        ):
            pass
