"""Record the transport that actually served authenticated Gateway requests."""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from knoa_platform.transport_health import TransportHealth


class TransportHealthMiddleware:
    """Tiny ASGI middleware; response completion is the request truth signal."""

    def __init__(self, app: Callable[..., Awaitable[Any]], *, health: TransportHealth) -> None:
        self.app = app
        self.health = health

    async def __call__(self, scope: dict[str, Any], receive: Callable[..., Awaitable[Any]], send: Callable[..., Awaitable[Any]]) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        headers = {key.lower(): value for key, value in scope.get("headers", [])}
        raw_transport = headers.get(b"x-knoa-transport", b"").decode("ascii", "ignore").strip().lower()
        if raw_transport not in {"mdns", "p2p", "relay"}:
            await self.app(scope, receive, send)
            return
        transport = raw_transport  # narrowed by the membership check above
        self.health.record(transport, "verification", ok=True)

        async def send_with_observation(message: dict[str, Any]) -> None:
            if message.get("type") == "http.response.start":
                status = int(message.get("status", 500))
                if status < 500:
                    self.health.record(transport, "request", ok=True)
                    self.health.activate(transport, reason=f"{transport} request completed")
                else:
                    self.health.record(transport, "request", ok=False, error=f"HTTP {status}")
            await send(message)

        await self.app(scope, receive, send_with_observation)


__all__ = ["TransportHealthMiddleware"]
