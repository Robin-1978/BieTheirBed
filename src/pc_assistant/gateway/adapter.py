"""Strict loopback HTTP surface for Secure Gateway device authentication."""
from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections import defaultdict, deque
from typing import Any

import uvicorn
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from pc_assistant.config import AppConfig
from pc_assistant.gateway.auth import (
    AuthenticatedGatewaySession,
    GatewayAuthenticationRejectedError,
    GatewayAuthenticationService,
    GatewayAuthRepository,
)
from pc_assistant.gateway.core import GatewayCoreBridge
from pc_assistant.gateway.identity import (
    DeviceAlreadyPairedError,
    GatewayIdentityRepository,
    PairingGrantRejectedError,
)
from pc_assistant.runtime import RuntimePaths
from pc_assistant.service.core_api import ArtifactInputRef
from pc_assistant.service.core_client import (
    CoreConnectionLostError,
    CoreRequestError,
    CoreRequestTimeoutError,
)
from pc_assistant.tasks import TaskState


logger = logging.getLogger(__name__)
_MAX_BODY_BYTES = 16 * 1024


class _RequestModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class _PairChallenge(_RequestModel):
    grant_id: str = Field(min_length=1, max_length=128)


class _PairComplete(_PairChallenge):
    grant_secret: str = Field(min_length=32, max_length=256)
    challenge_id: str = Field(min_length=1, max_length=128)
    nonce: str = Field(min_length=32, max_length=256)
    display_name: str = Field(min_length=1, max_length=80)
    public_key: str = Field(min_length=40, max_length=64)
    signature: str = Field(min_length=80, max_length=128)


class _AuthChallenge(_RequestModel):
    device_id: str = Field(min_length=1, max_length=128)


class _AuthComplete(_AuthChallenge):
    challenge_id: str = Field(min_length=1, max_length=128)
    nonce: str = Field(min_length=32, max_length=256)
    signature: str = Field(min_length=80, max_length=128)


class _CreateTask(_RequestModel):
    session_handle: str = Field(min_length=1, max_length=256)
    input: str = Field(default="", max_length=200_000)
    attachments: tuple[ArtifactInputRef, ...] = Field(default=(), max_length=8)
    tools_enabled: bool = True
    priority: int = Field(default=0, ge=0, le=9)
    parent_task_id: str = Field(default="", max_length=128)

    def require_content(self) -> None:
        if not self.input.strip() and not self.attachments:
            raise ValueError("Task request requires input or an attachment")


class _CancelTask(_RequestModel):
    reason: str = Field(default="", max_length=1000)


class _ResolveApproval(_RequestModel):
    approved: bool


class _TaskListQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_handle: str = Field(default="", max_length=256)
    state: TaskState | None = None
    limit: int = Field(default=50, ge=1, le=100)
    cursor: str = Field(default="", max_length=512)


class _WindowLimiter:
    def __init__(self, *, clock=time.monotonic) -> None:
        self._clock = clock
        self._requests: dict[str, deque[float]] = defaultdict(deque)

    def allow(self, key: str, *, limit: int, window_seconds: float = 60.0) -> bool:
        now = float(self._clock())
        bucket = self._requests[key]
        cutoff = now - window_seconds
        while bucket and bucket[0] <= cutoff:
            bucket.popleft()
        if len(bucket) >= limit:
            return False
        bucket.append(now)
        return True


class _EmbeddedUvicornServer(uvicorn.Server):
    @contextlib.contextmanager
    def capture_signals(self):
        yield


class SecureGatewayAdapter:
    """Expose authentication only on loopback; no Core command proxy yet."""

    name = "secure_gateway"

    def __init__(
        self,
        config: AppConfig,
        *,
        authentication: GatewayAuthenticationService | None = None,
        core: GatewayCoreBridge | None = None,
        limiter: _WindowLimiter | None = None,
    ) -> None:
        if not config.gateway_enabled:
            raise ValueError("SecureGatewayAdapter requires gateway_enabled")
        if config.gateway_host.strip().lower() not in {
            "127.0.0.1",
            "localhost",
            "::1",
        }:
            raise ValueError("Secure Gateway must bind to loopback before TLS")
        self._config = config
        if authentication is None:
            database = RuntimePaths.from_root(config.runtime_root).data / "gateway.db"
            identities = GatewayIdentityRepository(database)
            authentication = GatewayAuthenticationService(
                identities,
                GatewayAuthRepository(database),
            )
        self._authentication = authentication
        self._core = core or GatewayCoreBridge(config)
        self._limiter = limiter or _WindowLimiter()
        self._server: _EmbeddedUvicornServer | None = None
        self._server_task: asyncio.Task[None] | None = None
        self.app = Starlette(
            routes=[
                Route("/health", self._health, methods=["GET"]),
                Route("/v1/pair/challenge", self._pair_challenge, methods=["POST"]),
                Route("/v1/pair/complete", self._pair_complete, methods=["POST"]),
                Route("/v1/auth/challenge", self._auth_challenge, methods=["POST"]),
                Route("/v1/auth/complete", self._auth_complete, methods=["POST"]),
                Route("/v1/session", self._session, methods=["GET"]),
                Route("/v1/sessions", self._create_session, methods=["POST"]),
                Route("/v1/tasks", self._create_task, methods=["POST"]),
                Route("/v1/tasks", self._list_tasks, methods=["GET"]),
                Route("/v1/tasks/{task_id:str}", self._get_task, methods=["GET"]),
                Route(
                    "/v1/tasks/{task_id:str}/cancel",
                    self._cancel_task,
                    methods=["POST"],
                ),
                Route(
                    "/v1/approvals/{approval_id:str}/resolve",
                    self._resolve_approval,
                    methods=["POST"],
                ),
            ]
        )

    @property
    def bound_port(self) -> int | None:
        if self._server is None or not self._server.servers:
            return None
        sockets = self._server.servers[0].sockets
        return int(sockets[0].getsockname()[1]) if sockets else None

    async def start(self) -> None:
        if self._server_task is not None:
            raise RuntimeError("SecureGatewayAdapter is already started")
        server = _EmbeddedUvicornServer(
            uvicorn.Config(
                self.app,
                host=self._config.gateway_host,
                port=self._config.gateway_port,
                log_config=None,
                access_log=False,
                lifespan="off",
            )
        )
        task = asyncio.create_task(server.serve(), name="knoa-secure-gateway")
        self._server, self._server_task = server, task
        try:
            for _ in range(500):
                if server.started:
                    logger.info(
                        "Secure Gateway listening on %s:%s",
                        self._config.gateway_host,
                        self.bound_port,
                    )
                    return
                if task.done():
                    await task
                    raise RuntimeError("Secure Gateway stopped during startup")
                await asyncio.sleep(0.01)
            raise TimeoutError("Secure Gateway startup timed out")
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
        await self._core.close()

    async def _health(self, _request: Request) -> JSONResponse:
        return JSONResponse({"status": "ok", "scope": "authentication"})

    async def _pair_challenge(self, request: Request) -> JSONResponse:
        parsed = await self._body(request, _PairChallenge, limit=20)
        if isinstance(parsed, JSONResponse):
            return parsed
        try:
            challenge = self._authentication.begin_pairing(parsed.grant_id)
        except (GatewayAuthenticationRejectedError, PairingGrantRejectedError):
            return JSONResponse({"error": "rejected"}, status_code=401)
        return self._challenge_response(challenge)

    async def _pair_complete(self, request: Request) -> JSONResponse:
        parsed = await self._body(request, _PairComplete, limit=10)
        if isinstance(parsed, JSONResponse):
            return parsed
        try:
            device = self._authentication.complete_pairing(**parsed.model_dump())
        except (
            GatewayAuthenticationRejectedError,
            PairingGrantRejectedError,
            DeviceAlreadyPairedError,
            ValueError,
        ):
            return JSONResponse({"error": "rejected"}, status_code=401)
        return JSONResponse(
            {"device_id": device.device_id, "principal_id": device.principal_id},
            status_code=201,
        )

    async def _auth_challenge(self, request: Request) -> JSONResponse:
        parsed = await self._body(request, _AuthChallenge, limit=30)
        if isinstance(parsed, JSONResponse):
            return parsed
        try:
            challenge = self._authentication.begin_authentication(parsed.device_id)
        except GatewayAuthenticationRejectedError:
            return JSONResponse({"error": "rejected"}, status_code=401)
        return self._challenge_response(challenge)

    async def _auth_complete(self, request: Request) -> JSONResponse:
        parsed = await self._body(request, _AuthComplete, limit=20)
        if isinstance(parsed, JSONResponse):
            return parsed
        try:
            session = self._authentication.complete_authentication(
                **parsed.model_dump(),
                session_ttl_seconds=self._config.gateway_session_ttl_seconds,
            )
        except (GatewayAuthenticationRejectedError, ValueError):
            return JSONResponse({"error": "rejected"}, status_code=401)
        return JSONResponse(
            {
                "token": session.token,
                "expires_at": session.expires_at,
                "device_id": session.device_id,
            }
        )

    async def _session(self, request: Request) -> JSONResponse:
        authenticated = self._authorize(request, limit=120)
        if isinstance(authenticated, JSONResponse):
            return authenticated
        return JSONResponse(
            {
                "session_id": authenticated.session_id,
                "device_id": authenticated.device.device_id,
                "principal_id": authenticated.device.principal_id,
                "expires_at": authenticated.expires_at,
            }
        )

    async def _create_session(self, request: Request) -> JSONResponse:
        authenticated = self._authorize(request, limit=30)
        if isinstance(authenticated, JSONResponse):
            return authenticated
        try:
            handle = await self._core.create_session(
                authenticated.device.principal_id
            )
        except Exception as exc:
            return self._core_error(exc)
        return JSONResponse({"session_handle": handle}, status_code=201)

    async def _create_task(self, request: Request) -> JSONResponse:
        authenticated = self._authorize(request, limit=60)
        if isinstance(authenticated, JSONResponse):
            return authenticated
        parsed = await self._parse_body(request, _CreateTask)
        if isinstance(parsed, JSONResponse):
            return parsed
        try:
            parsed.require_content()
            accepted = await self._core.create_task(
                authenticated.device.principal_id,
                parsed.session_handle,
                parsed.input,
                parsed.attachments,
                tools_enabled=parsed.tools_enabled,
                priority=parsed.priority,
                parent_task_id=parsed.parent_task_id,
            )
        except ValueError:
            return JSONResponse({"error": "invalid_request"}, status_code=400)
        except Exception as exc:
            return self._core_error(exc)
        return JSONResponse(
            {"task_id": accepted.task_id, "state": accepted.state.value},
            status_code=202,
        )

    async def _list_tasks(self, request: Request) -> JSONResponse:
        authenticated = self._authorize(request, limit=120)
        if isinstance(authenticated, JSONResponse):
            return authenticated
        try:
            query = _TaskListQuery.model_validate(dict(request.query_params))
        except ValidationError:
            return JSONResponse({"error": "invalid_request"}, status_code=400)
        try:
            result = await self._core.list_tasks(
                authenticated.device.principal_id,
                session_handle=query.session_handle,
                state=query.state,
                limit=query.limit,
                cursor=query.cursor,
            )
        except Exception as exc:
            return self._core_error(exc)
        return JSONResponse(
            {
                "tasks": [task.model_dump(mode="json") for task in result.tasks],
                "next_cursor": result.next_cursor,
            }
        )

    async def _get_task(self, request: Request) -> JSONResponse:
        authenticated = self._authorize(request, limit=120)
        if isinstance(authenticated, JSONResponse):
            return authenticated
        task_id = self._path_identifier(request, "task_id")
        if task_id is None:
            return JSONResponse({"error": "invalid_request"}, status_code=400)
        try:
            task = await self._core.get_task(
                authenticated.device.principal_id,
                task_id,
            )
        except Exception as exc:
            return self._core_error(exc)
        return JSONResponse({"task": task.model_dump(mode="json")})

    async def _cancel_task(self, request: Request) -> JSONResponse:
        authenticated = self._authorize(request, limit=30)
        if isinstance(authenticated, JSONResponse):
            return authenticated
        task_id = self._path_identifier(request, "task_id")
        if task_id is None:
            return JSONResponse({"error": "invalid_request"}, status_code=400)
        parsed = await self._parse_body(request, _CancelTask)
        if isinstance(parsed, JSONResponse):
            return parsed
        try:
            result = await self._core.cancel_task(
                authenticated.device.principal_id,
                task_id,
                reason=parsed.reason,
            )
        except Exception as exc:
            return self._core_error(exc)
        return JSONResponse(result.result.model_dump(mode="json"))

    async def _resolve_approval(self, request: Request) -> JSONResponse:
        authenticated = self._authorize(request, limit=30)
        if isinstance(authenticated, JSONResponse):
            return authenticated
        approval_id = self._path_identifier(request, "approval_id")
        if approval_id is None:
            return JSONResponse({"error": "invalid_request"}, status_code=400)
        parsed = await self._parse_body(request, _ResolveApproval)
        if isinstance(parsed, JSONResponse):
            return parsed
        try:
            result = await self._core.resolve_approval(
                authenticated.device.principal_id,
                approval_id,
                approved=parsed.approved,
            )
        except Exception as exc:
            return self._core_error(exc)
        return JSONResponse(
            {
                "approval_id": result.approval_id,
                "resolved": result.resolved,
                "state": result.state.value,
            }
        )

    def _authorize(
        self,
        request: Request,
        *,
        limit: int,
    ) -> AuthenticatedGatewaySession | JSONResponse:
        authorization = request.headers.get("Authorization", "")
        scheme, _space, token = authorization.partition(" ")
        if scheme.lower() != "bearer" or not token:
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        try:
            session = self._authentication.authenticate_session(token)
        except GatewayAuthenticationRejectedError:
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        key = f"authorized:{request.url.path}:{session.device.device_id}"
        if not self._limiter.allow(key, limit=limit):
            return JSONResponse({"error": "rate_limited"}, status_code=429)
        return session

    async def _body(
        self,
        request: Request,
        model: type[_RequestModel],
        *,
        limit: int,
    ) -> _RequestModel | JSONResponse:
        host = request.client.host if request.client is not None else "unknown"
        key = f"{request.url.path}:{host}"
        if not self._limiter.allow(key, limit=limit):
            return JSONResponse({"error": "rate_limited"}, status_code=429)
        return await self._parse_body(request, model)

    async def _parse_body(
        self,
        request: Request,
        model: type[_RequestModel],
    ) -> _RequestModel | JSONResponse:
        content_type = request.headers.get("Content-Type", "").partition(";")[0]
        if content_type.strip().lower() != "application/json":
            return JSONResponse({"error": "unsupported_media_type"}, status_code=415)
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > _MAX_BODY_BYTES:
                return JSONResponse({"error": "payload_too_large"}, status_code=413)
        try:
            return model.model_validate_json(bytes(body))
        except ValidationError:
            return JSONResponse({"error": "invalid_request"}, status_code=400)

    @staticmethod
    def _path_identifier(request: Request, name: str) -> str | None:
        value = str(request.path_params.get(name, "")).strip()
        if not value or len(value) > 128:
            return None
        return value

    @staticmethod
    def _core_error(exc: Exception) -> JSONResponse:
        if isinstance(exc, CoreRequestError):
            if exc.code in {
                "task_not_found",
                "session_not_found",
                "approval_not_found",
            }:
                return JSONResponse({"error": "not_found"}, status_code=404)
            if exc.code in {"invalid_request", "invalid_state"}:
                return JSONResponse({"error": "rejected"}, status_code=422)
        if isinstance(
            exc,
            (CoreConnectionLostError, CoreRequestTimeoutError),
        ):
            return JSONResponse({"error": "unavailable"}, status_code=503)
        logger.warning("Secure Gateway Core request failed", exc_info=exc)
        return JSONResponse({"error": "unavailable"}, status_code=503)

    @staticmethod
    def _challenge_response(challenge: Any) -> JSONResponse:
        return JSONResponse(
            {
                "challenge_id": challenge.challenge_id,
                "nonce": challenge.nonce,
                "expires_at": challenge.expires_at,
            }
        )
