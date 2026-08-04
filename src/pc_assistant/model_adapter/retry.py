from __future__ import annotations

import asyncio
from typing import Any

import httpx


async def request_with_retry(
    max_retries: int,
    client: httpx.AsyncClient,
    method: str,
    url: str,
    **kwargs: Any,
) -> httpx.Response:
    last_error: Exception = RuntimeError("Request failed with no error information")
    for attempt in range(max_retries):
        try:
            if method == "POST":
                resp = await client.post(url, **kwargs)
            else:
                resp = await client.get(url, **kwargs)
            resp.raise_for_status()
            return resp
        except asyncio.CancelledError:
            raise
        except (httpx.ConnectError, httpx.ReadError, httpx.WriteError, httpx.PoolTimeout) as e:
            last_error = e
            if attempt < max_retries - 1:
                delay = 0.5 * (2 ** attempt)
                await asyncio.sleep(delay)
        except httpx.HTTPStatusError as e:
            if e.response.status_code in (429, 500, 502, 503, 504):
                last_error = e
                if attempt < max_retries - 1:
                    delay = 0.5 * (2 ** attempt)
                    await asyncio.sleep(delay)
            else:
                raise
    raise last_error
