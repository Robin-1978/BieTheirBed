"""Authenticated HTTP webhook ingress for durable business Triggers."""
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import hmac
import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from pc_assistant.config import AppConfig
from pc_assistant.network_tls import is_loopback_host
from pc_assistant.runtime import RuntimePaths
from pc_assistant.service.core_client import CoreClient, CoreRequestError
from pc_assistant.service.credentials import (
    issue_principal_credential,
    resolve_local_service_token,
)


logger = logging.getLogger(__name__)
_MAX_WEBHOOK_BODY_BYTES = 128 * 1024
_EVENT_ID_HEADER = "X-Knoa-Event-Id"
_SIGNATURE_HEADER = "X-Knoa-Signature"


class TriggerClient(Protocol):
    is_connected: bool

    async def fire_trigger(
        self,
        trigger_id: str,
        external_event_id: str,
        payload: dict[str, Any],
    ) -> Any: ...

    async def disconnect(self) -> None: ...


ClientFactory = Callable[[str], Awaitable[TriggerClient]]


class _EmbeddedUvicornServer(uvicorn.Server):
    """Leave process signal ownership with ApplicationDaemon."""

    @contextlib.contextmanager
    def capture_signals(self):
        yield


class WebhookAdapter:
    """Verify external requests and forward them through authenticated Core API."""

    name = "webhook"

    def __init__(
        self,
        config: AppConfig,
        *,
        client_factory: ClientFactory | None = None,
    ) -> None:
        if not config.webhook_enabled:
            raise ValueError("WebhookAdapter requires webhook_enabled")
        if not is_loopback_host(config.webhook_host):
            raise ValueError(
                "Webhook adapter must bind to loopback; expose it through a TLS reverse proxy"
            )
        self._config = config
        self._paths = RuntimePaths.from_root(config.runtime_root)
        self._routes = dict(config.webhook_routes)
        self._secrets = {
            route_id: route.resolved_secret().encode("utf-8")
            for route_id, route in self._routes.items()
        }
        self._client_factory = client_factory or self._connect_client
        self._clients: dict[str, TriggerClient] = {}
        self._client_locks: dict[str, asyncio.Lock] = {}
        self._server: _EmbeddedUvicornServer | None = None
        self._server_task: asyncio.Task[None] | None = None
        self.app = Starlette(
            routes=[
                Route("/health", self._health, methods=["GET"]),
                Route("/hooks/{route_id:str}", self._receive, methods=["POST"]),
            ]
        )

    @property
    def started(self) -> bool:
        return self._server_task is not None

    @property
    def bound_port(self) -> int | None:
        server = self._server
        if server is None or not server.servers:
            return None
        sockets = server.servers[0].sockets
        if not sockets:
            return None
        return int(sockets[0].getsockname()[1])

    async def start(self) -> None:
        if self._server_task is not None:
            raise RuntimeError("WebhookAdapter is already started")
        server = _EmbeddedUvicornServer(
            uvicorn.Config(
                self.app,
                host=self._config.webhook_host,
                port=self._config.webhook_port,
                log_config=None,
                access_log=False,
                lifespan="off",
            )
        )
        task = asyncio.create_task(server.serve(), name="knoa-webhook-adapter")
        self._server = server
        self._server_task = task
        try:
            for _ in range(500):
                if server.started:
                    logger.info(
                        "Webhook adapter listening on %s:%s",
                        self._config.webhook_host,
                        self.bound_port,
                    )
                    return
                if task.done():
                    await task
                    raise RuntimeError("Webhook adapter stopped during startup")
                await asyncio.sleep(0.01)
            raise TimeoutError("Webhook adapter startup timed out")
        except BaseException:
            await self.stop()
            raise

    async def stop(self) -> None:
        server, self._server = self._server, None
        task, self._server_task = self._server_task, None
        if server is not None:
            server.should_exit = True
        if task is not None:
            try:
                await asyncio.wait_for(task, timeout=5.0)
            except TimeoutError:
                if server is not None:
                    server.force_exit = True
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        clients, self._clients = tuple(self._clients.values()), {}
        await asyncio.gather(
            *(client.disconnect() for client in clients),
            return_exceptions=True,
        )

    async def _health(self, _request: Request) -> JSONResponse:
        return JSONResponse({"status": "ok"})

    async def _receive(self, request: Request) -> JSONResponse:
        route_id = str(request.path_params.get("route_id", ""))
        route = self._routes.get(route_id)
        if route is None:
            return JSONResponse({"error": "not_found"}, status_code=404)
        event_id = request.headers.get(_EVENT_ID_HEADER, "").strip()
        if (
            not event_id
            or len(event_id) > 256
            or "\r" in event_id
            or "\n" in event_id
        ):
            return JSONResponse({"error": "invalid_event_id"}, status_code=400)
        content_type = request.headers.get("Content-Type", "").partition(";")[0]
        if content_type.strip().lower() != "application/json":
            return JSONResponse({"error": "unsupported_media_type"}, status_code=415)
        content_length = request.headers.get("Content-Length", "").strip()
        if content_length:
            try:
                if int(content_length) > _MAX_WEBHOOK_BODY_BYTES:
                    return JSONResponse(
                        {"error": "payload_too_large"},
                        status_code=413,
                    )
            except ValueError:
                return JSONResponse(
                    {"error": "invalid_content_length"},
                    status_code=400,
                )
        signature = request.headers.get(_SIGNATURE_HEADER, "").strip().lower()
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > _MAX_WEBHOOK_BODY_BYTES:
                return JSONResponse({"error": "payload_too_large"}, status_code=413)
        expected = "sha256=" + hmac.new(
            self._secrets[route_id],
            event_id.encode("utf-8") + b"\n" + bytes(body),
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(signature, expected):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        try:
            payload = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError):
            return JSONResponse({"error": "invalid_json"}, status_code=400)
        if not isinstance(payload, dict):
            return JSONResponse({"error": "invalid_payload"}, status_code=400)
        try:
            client = await self._client_for(route.principal_id)
            event = await client.fire_trigger(
                route.trigger_id,
                event_id,
                payload,
            )
        except CoreRequestError as exc:
            if exc.code in {"trigger_not_found", "invalid_request"}:
                return JSONResponse({"error": "rejected"}, status_code=422)
            logger.warning("Webhook Core request failed code=%s", exc.code)
            return JSONResponse({"error": "unavailable"}, status_code=503)
        except Exception:
            logger.warning("Webhook delivery failed route=%s", route_id, exc_info=True)
            return JSONResponse({"error": "unavailable"}, status_code=503)
        return JSONResponse(
            {
                "accepted": True,
                "trigger_event_id": event.trigger_event_id,
                "state": event.state.value,
                "task_id": event.task_id,
            },
            status_code=202,
        )

    async def _client_for(self, principal_id: str) -> TriggerClient:
        lock = self._client_locks.setdefault(principal_id, asyncio.Lock())
        async with lock:
            current = self._clients.get(principal_id)
            if current is not None and current.is_connected:
                return current
            if current is not None:
                await current.disconnect()
            client = await self._client_factory(principal_id)
            self._clients[principal_id] = client
            return client

    async def _connect_client(self, principal_id: str) -> CoreClient:
        signing_key = resolve_local_service_token(self._paths)
        credential = issue_principal_credential(signing_key, principal_id)
        return await CoreClient.connect(
            f"ws://{self._config.service_host}:{self._config.service_port}",
            credential,
        )
