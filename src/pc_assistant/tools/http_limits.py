"""Bounded response readers shared by built-in HTTP tools."""
from __future__ import annotations

import json
from typing import Any


class HttpResponseTooLargeError(ValueError):
    pass


async def read_limited_bytes(response: Any, max_bytes: int) -> bytes:
    content_length = response.headers.get("content-length")
    if content_length is not None:
        try:
            declared_size = int(content_length)
        except ValueError:
            declared_size = 0
        if declared_size > max_bytes:
            raise HttpResponseTooLargeError(
                f"HTTP response exceeds {max_bytes} byte limit"
            )

    body = bytearray()
    async for chunk in response.aiter_bytes():
        if len(body) + len(chunk) > max_bytes:
            raise HttpResponseTooLargeError(
                f"HTTP response exceeds {max_bytes} byte limit"
            )
        body.extend(chunk)
    return bytes(body)


async def read_limited_text(response: Any, max_bytes: int) -> str:
    body = await read_limited_bytes(response, max_bytes)
    return body.decode(response.encoding or "utf-8", errors="replace")


async def read_limited_json(response: Any, max_bytes: int) -> Any:
    return json.loads(await read_limited_bytes(response, max_bytes))


async def iter_limited_lines(
    response: Any,
    *,
    max_line_bytes: int,
    max_total_bytes: int,
):
    buffer = bytearray()
    total = 0
    async for chunk in response.aiter_bytes():
        total += len(chunk)
        if total > max_total_bytes:
            raise HttpResponseTooLargeError(
                f"HTTP stream exceeds {max_total_bytes} byte limit"
            )
        buffer.extend(chunk)
        while True:
            newline = buffer.find(b"\n")
            if newline < 0:
                break
            raw_line = bytes(buffer[:newline])
            del buffer[: newline + 1]
            if raw_line.endswith(b"\r"):
                raw_line = raw_line[:-1]
            if len(raw_line) > max_line_bytes:
                raise HttpResponseTooLargeError(
                    f"HTTP stream line exceeds {max_line_bytes} byte limit"
                )
            yield raw_line.decode("utf-8")
        if len(buffer) > max_line_bytes:
            raise HttpResponseTooLargeError(
                f"HTTP stream line exceeds {max_line_bytes} byte limit"
            )
    if buffer:
        yield bytes(buffer).decode("utf-8")
