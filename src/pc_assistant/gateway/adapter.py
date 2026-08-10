"""Fail-closed HTTP/TLS surface for Secure Gateway mobile access."""
from __future__ import annotations

import asyncio
import base64
import binascii
import contextlib
import json
import logging
import os
import re
import stat
import time
from collections import defaultdict, deque
from pathlib import Path
from typing import Any

import uvicorn
from pydantic import ValidationError
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response, StreamingResponse
from starlette.routing import Route

from pc_assistant.config import AppConfig
from pc_assistant.gateway.audit import GatewayAuditRepository
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
from pc_assistant.gateway.push import (
    ExpoPushTransport,
    GatewayPushDispatcher,
    GatewayPushRepository,
    PushTransport,
)
from pc_assistant.gateway.releases import AndroidReleaseRepository
from pc_assistant.network_tls import is_loopback_host
from pc_assistant.runtime import RuntimePaths
from pc_assistant.gateway.protocol import (
    ArtifactDownloadQuery,
    ArtifactUploadQuery,
    AuditQuery,
    AuthChallengeRequest,
    AuthCompleteRequest,
    CancelTaskRequest,
    CreateTaskRequest,
    EventQuery,
    GatewayRequest,
    PairChallengeRequest,
    PairCompleteRequest,
    PauseTaskRequest,
    RegisterPushRequest,
    ResolveApprovalRequest,
    ResumeTaskRequest,
    RetryTaskRequest,
    RuntimeQuery,
    TaskListQuery,
    TaskEventQuery,
)
from pc_assistant.service.core_client import (
    CoreConnectionLostError,
    CoreRequestError,
    CoreRequestTimeoutError,
)
from pc_assistant.tasks import TaskOrigin


logger = logging.getLogger(__name__)
_MAX_BODY_BYTES = 16 * 1024


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
    """Expose a bounded mobile protocol without allowing plaintext remote binds."""

    name = "secure_gateway"

    def __init__(
        self,
        config: AppConfig,
        *,
        authentication: GatewayAuthenticationService | None = None,
        core: GatewayCoreBridge | None = None,
        limiter: _WindowLimiter | None = None,
        audit: GatewayAuditRepository | None = None,
        push_repository: GatewayPushRepository | None = None,
        push_transport: PushTransport | None = None,
        release_repository: AndroidReleaseRepository | None = None,
        event_heartbeat_seconds: float = 15.0,
    ) -> None:
        if not config.gateway_enabled:
            raise ValueError("SecureGatewayAdapter requires gateway_enabled")
        self._tls_cert_file: Path | None = None
        self._tls_key_file: Path | None = None
        if config.gateway_remote_enabled:
            self._tls_cert_file = self._tls_file(
                config.gateway_tls_cert_file,
                label="certificate",
                private=False,
            )
            self._tls_key_file = self._tls_file(
                config.gateway_tls_key_file,
                label="private key",
                private=True,
            )
        elif not is_loopback_host(config.gateway_host):
            raise ValueError("Secure Gateway must bind to loopback before TLS")
        self._config = config
        database = RuntimePaths.from_root(config.runtime_root).data / "gateway.db"
        if authentication is None:
            identities = GatewayIdentityRepository(database)
            authentication = GatewayAuthenticationService(
                identities,
                GatewayAuthRepository(database),
            )
        self._authentication = authentication
        self._audit = audit or GatewayAuditRepository(database)
        self._core = core or GatewayCoreBridge(config)
        self._push_repository = push_repository or GatewayPushRepository(database)
        self._push_dispatcher = GatewayPushDispatcher(
            config.owner_principal_id,
            self._core,
            self._push_repository,
            push_transport or ExpoPushTransport(),
        )
        self._releases = release_repository or AndroidReleaseRepository(
            RuntimePaths.from_root(config.runtime_root).data
            / "mobile-releases"
            / "android"
        )
        self._limiter = limiter or _WindowLimiter()
        self._event_heartbeat_seconds = max(0.01, event_heartbeat_seconds)
        self._active_event_streams: dict[str, int] = defaultdict(int)
        self._server: _EmbeddedUvicornServer | None = None
        self._server_task: asyncio.Task[None] | None = None
        self.app = Starlette(
            routes=[
                Route("/health", self._health, methods=["GET"]),
                Route("/openapi.json", self._openapi, methods=["GET"]),
                Route("/v1/pair/challenge", self._pair_challenge, methods=["POST"]),
                Route("/v1/pair/complete", self._pair_complete, methods=["POST"]),
                Route("/v1/auth/challenge", self._auth_challenge, methods=["POST"]),
                Route("/v1/auth/complete", self._auth_complete, methods=["POST"]),
                Route("/v1/session", self._session, methods=["GET"]),
                Route("/v1/sessions", self._create_session, methods=["POST"]),
                Route("/v1/tasks", self._create_task, methods=["POST"]),
                Route("/v1/tasks", self._list_tasks, methods=["GET"]),
                Route("/v1/events", self._events, methods=["GET"]),
                Route("/v1/artifacts", self._upload_artifact, methods=["POST"]),
                Route(
                    "/v1/artifacts/{artifact_id:str}",
                    self._download_artifact,
                    methods=["GET"],
                ),
                Route("/v1/tasks/{task_id:str}", self._get_task, methods=["GET"]),
                Route(
                    "/v1/tasks/{task_id:str}/events",
                    self._task_events,
                    methods=["GET"],
                ),
                Route(
                    "/v1/tasks/{task_id:str}/cancel",
                    self._cancel_task,
                    methods=["POST"],
                ),
                Route(
                    "/v1/tasks/{task_id:str}/pause",
                    self._pause_task,
                    methods=["POST"],
                ),
                Route(
                    "/v1/tasks/{task_id:str}/resume",
                    self._resume_task,
                    methods=["POST"],
                ),
                Route(
                    "/v1/tasks/{task_id:str}/retry",
                    self._retry_task,
                    methods=["POST"],
                ),
                Route(
                    "/v1/artifacts/{artifact_id:str}/transcribe",
                    self._transcribe_artifact,
                    methods=["POST"],
                ),
                Route("/v1/runtime/status", self._runtime_status, methods=["GET"]),
                Route("/v1/tools", self._list_tools, methods=["GET"]),
                Route(
                    "/v1/mobile/releases/android/latest",
                    self._latest_android_release,
                    methods=["GET"],
                ),
                Route(
                    "/v1/mobile/releases/android/{version_code:str}/package",
                    self._download_android_release,
                    methods=["GET"],
                ),
                Route("/v1/device/audit", self._device_audit, methods=["GET"]),
                Route(
                    "/v1/device/push",
                    self._device_push,
                    methods=["PUT", "DELETE"],
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
                ssl_certfile=(
                    None if self._tls_cert_file is None else str(self._tls_cert_file)
                ),
                ssl_keyfile=(
                    None if self._tls_key_file is None else str(self._tls_key_file)
                ),
            )
        )
        task = asyncio.create_task(server.serve(), name="knoa-secure-gateway")
        self._server, self._server_task = server, task
        try:
            for _ in range(500):
                if server.started:
                    logger.info(
                        "Secure Gateway listening on %s://%s:%s",
                        "https" if self._tls_cert_file is not None else "http",
                        self._config.gateway_host,
                        self.bound_port,
                    )
                    self._push_dispatcher.start()
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
        await self._push_dispatcher.stop()
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

    async def _openapi(self, _request: Request) -> JSONResponse:
        from pc_assistant.gateway.openapi import gateway_openapi_schema

        return JSONResponse(gateway_openapi_schema())

    async def _pair_challenge(self, request: Request) -> JSONResponse:
        parsed = await self._body(request, PairChallengeRequest, limit=20)
        if isinstance(parsed, JSONResponse):
            return parsed
        try:
            challenge = self._authentication.begin_pairing(parsed.grant_id)
        except (GatewayAuthenticationRejectedError, PairingGrantRejectedError):
            return JSONResponse({"error": "rejected"}, status_code=401)
        return self._challenge_response(challenge)

    async def _pair_complete(self, request: Request) -> JSONResponse:
        parsed = await self._body(request, PairCompleteRequest, limit=10)
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
        self._record_audit(
            "paired",
            request=request,
            device_id=device.device_id,
            principal_id=device.principal_id,
        )
        return JSONResponse(
            {"device_id": device.device_id, "principal_id": device.principal_id},
            status_code=201,
        )

    async def _auth_challenge(self, request: Request) -> JSONResponse:
        parsed = await self._body(request, AuthChallengeRequest, limit=30)
        if isinstance(parsed, JSONResponse):
            return parsed
        try:
            challenge = self._authentication.begin_authentication(parsed.device_id)
        except GatewayAuthenticationRejectedError:
            return JSONResponse({"error": "rejected"}, status_code=401)
        return self._challenge_response(challenge)

    async def _auth_complete(self, request: Request) -> JSONResponse:
        parsed = await self._body(request, AuthCompleteRequest, limit=20)
        if isinstance(parsed, JSONResponse):
            return parsed
        try:
            session = self._authentication.complete_authentication(
                **parsed.model_dump(),
                session_ttl_seconds=self._config.gateway_session_ttl_seconds,
            )
        except (GatewayAuthenticationRejectedError, ValueError):
            self._record_audit("session_rejected", request=request)
            return JSONResponse({"error": "rejected"}, status_code=401)
        self._record_audit(
            "authenticated",
            request=request,
            device_id=session.device_id,
            principal_id=session.principal_id,
        )
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
        parsed = await self._parse_body(request, CreateTaskRequest)
        if isinstance(parsed, JSONResponse):
            return parsed
        try:
            parsed.require_content()
            session_handle = parsed.session_handle
            origin = TaskOrigin.CHAT
            if parsed.kind == "task":
                if parsed.attachments:
                    return JSONResponse(
                        {"error": "background_attachments_not_supported"},
                        status_code=422,
                    )
                session_handle = await self._core.create_session(
                    authenticated.device.principal_id,
                    activate=False,
                )
                origin = TaskOrigin.USER
            accepted = await self._core.create_task(
                authenticated.device.principal_id,
                session_handle,
                parsed.input,
                parsed.attachments,
                tools_enabled=parsed.tools_enabled,
                priority=parsed.priority,
                parent_task_id=parsed.parent_task_id,
                origin=origin,
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
            query = TaskListQuery.model_validate(dict(request.query_params))
        except ValidationError:
            return JSONResponse({"error": "invalid_request"}, status_code=400)
        try:
            origins = {
                "all": (),
                "chat": (TaskOrigin.CHAT,),
                "task": (
                    TaskOrigin.USER,
                    TaskOrigin.AGENT,
                    TaskOrigin.SCHEDULED,
                    TaskOrigin.EVENT,
                ),
            }[query.kind]
            result = await self._core.list_tasks(
                authenticated.device.principal_id,
                session_handle=query.session_handle,
                state=query.state,
                origins=origins,
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

    async def _task_events(self, request: Request) -> JSONResponse:
        authenticated = self._authorize(request, limit=120)
        if isinstance(authenticated, JSONResponse):
            return authenticated
        task_id = self._path_identifier(request, "task_id")
        if task_id is None:
            return JSONResponse({"error": "invalid_request"}, status_code=400)
        try:
            query = TaskEventQuery.model_validate(dict(request.query_params))
            events = await self._core.task_events(
                authenticated.device.principal_id,
                task_id,
                after_seq=query.after_seq,
            )
        except ValidationError:
            return JSONResponse({"error": "invalid_request"}, status_code=400)
        except Exception as exc:
            return self._core_error(exc)
        return JSONResponse(
            {"events": [event.model_dump(mode="json") for event in events]}
        )

    async def _cancel_task(self, request: Request) -> JSONResponse:
        authenticated = self._authorize(request, limit=30)
        if isinstance(authenticated, JSONResponse):
            return authenticated
        task_id = self._path_identifier(request, "task_id")
        if task_id is None:
            return JSONResponse({"error": "invalid_request"}, status_code=400)
        parsed = await self._parse_body(request, CancelTaskRequest)
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

    async def _pause_task(self, request: Request) -> JSONResponse:
        authenticated = self._authorize(request, limit=30)
        if isinstance(authenticated, JSONResponse):
            return authenticated
        task_id = self._path_identifier(request, "task_id")
        if task_id is None:
            return JSONResponse({"error": "invalid_request"}, status_code=400)
        parsed = await self._parse_body(request, PauseTaskRequest)
        if isinstance(parsed, JSONResponse):
            return parsed
        try:
            result = await self._core.pause_task(
                authenticated.device.principal_id,
                task_id,
                reason=parsed.reason,
            )
        except Exception as exc:
            return self._core_error(exc)
        return JSONResponse(result.result.model_dump(mode="json"))

    async def _resume_task(self, request: Request) -> JSONResponse:
        authenticated = self._authorize(request, limit=30)
        if isinstance(authenticated, JSONResponse):
            return authenticated
        task_id = self._path_identifier(request, "task_id")
        if task_id is None:
            return JSONResponse({"error": "invalid_request"}, status_code=400)
        parsed = await self._parse_body(request, ResumeTaskRequest)
        if isinstance(parsed, JSONResponse):
            return parsed
        try:
            result = await self._core.resume_task(
                authenticated.device.principal_id,
                task_id,
                reason=parsed.reason,
                acknowledge_outcome_unknown=parsed.acknowledge_outcome_unknown,
            )
        except Exception as exc:
            return self._core_error(exc)
        return JSONResponse({"accepted": True, "state": result.state.value})

    async def _retry_task(self, request: Request) -> JSONResponse:
        authenticated = self._authorize(request, limit=30)
        if isinstance(authenticated, JSONResponse):
            return authenticated
        task_id = self._path_identifier(request, "task_id")
        if task_id is None:
            return JSONResponse({"error": "invalid_request"}, status_code=400)
        parsed = await self._parse_body(request, RetryTaskRequest)
        if isinstance(parsed, JSONResponse):
            return parsed
        try:
            result = await self._core.retry_task(
                authenticated.device.principal_id,
                task_id,
                reason=parsed.reason,
            )
        except ValueError:
            return JSONResponse({"error": "rejected"}, status_code=422)
        except Exception as exc:
            return self._core_error(exc)
        return JSONResponse(
            {"task_id": result.task_id, "state": result.state.value},
            status_code=202,
        )

    async def _transcribe_artifact(self, request: Request) -> JSONResponse:
        authenticated = self._authorize(request, limit=20)
        if isinstance(authenticated, JSONResponse):
            return authenticated
        artifact_id = self._path_identifier(request, "artifact_id")
        if artifact_id is None:
            return JSONResponse({"error": "invalid_request"}, status_code=400)
        try:
            query = RuntimeQuery.model_validate(dict(request.query_params))
            result = await self._core.transcribe_artifact(
                authenticated.device.principal_id,
                query.session_handle,
                artifact_id,
            )
        except ValidationError:
            return JSONResponse({"error": "invalid_request"}, status_code=400)
        except Exception as exc:
            return self._core_error(exc)
        return JSONResponse({"result": result.model_dump(mode="json")})

    async def _runtime_status(self, request: Request) -> JSONResponse:
        authenticated = self._authorize(request, limit=120)
        if isinstance(authenticated, JSONResponse):
            return authenticated
        try:
            query = RuntimeQuery.model_validate(dict(request.query_params))
            result = await self._core.status(
                authenticated.device.principal_id,
                query.session_handle,
            )
        except ValidationError:
            return JSONResponse({"error": "invalid_request"}, status_code=400)
        except Exception as exc:
            return self._core_error(exc)
        return JSONResponse({"result": result.model_dump(mode="json")})

    async def _list_tools(self, request: Request) -> JSONResponse:
        authenticated = self._authorize(request, limit=120)
        if isinstance(authenticated, JSONResponse):
            return authenticated
        try:
            query = RuntimeQuery.model_validate(dict(request.query_params))
            result = await self._core.list_tools(
                authenticated.device.principal_id,
                query.session_handle,
            )
        except ValidationError:
            return JSONResponse({"error": "invalid_request"}, status_code=400)
        except Exception as exc:
            return self._core_error(exc)
        return JSONResponse({"result": result.model_dump(mode="json")})

    async def _latest_android_release(self, request: Request) -> JSONResponse:
        authenticated = self._authorize(request, limit=30)
        if isinstance(authenticated, JSONResponse):
            return authenticated
        try:
            release = await asyncio.to_thread(self._releases.latest)
        except LookupError:
            return JSONResponse({"error": "not_found"}, status_code=404)
        assert release is not None
        return JSONResponse(
            {
                "platform": "android",
                "channel": "personal",
                "version_name": release.version_name,
                "version_code": release.version_code,
                "min_supported_version_code": release.min_supported_version_code,
                "size_bytes": release.size_bytes,
                "sha256": release.sha256,
                "published_at": release.published_at,
                "release_notes": release.release_notes,
                "download_path": (
                    f"/v1/mobile/releases/android/{release.version_code}/package"
                ),
            }
        )

    async def _download_android_release(
        self, request: Request
    ) -> JSONResponse | FileResponse:
        authenticated = self._authorize(request, limit=20)
        if isinstance(authenticated, JSONResponse):
            return authenticated
        raw_version_code = str(request.path_params.get("version_code", ""))
        if not raw_version_code.isascii() or not raw_version_code.isdecimal():
            return JSONResponse({"error": "invalid_request"}, status_code=400)
        version_code = int(raw_version_code)
        if version_code < 1 or version_code > 2_100_000_000:
            return JSONResponse({"error": "invalid_request"}, status_code=400)
        try:
            release = await asyncio.to_thread(self._releases.get, version_code)
            package = await asyncio.to_thread(self._releases.package_path, release)
            metadata = await asyncio.to_thread(package.stat)
        except LookupError:
            return JSONResponse({"error": "not_found"}, status_code=404)
        return FileResponse(
            package,
            media_type="application/vnd.android.package-archive",
            filename=f"knoa-{release.version_name}.apk",
            stat_result=metadata,
            headers={
                "Cache-Control": "private, no-cache",
                "ETag": f'"{release.sha256}"',
                "X-Knoa-SHA256": release.sha256,
            },
        )

    async def _device_audit(self, request: Request) -> JSONResponse:
        authenticated = self._authorize(request, limit=60)
        if isinstance(authenticated, JSONResponse):
            return authenticated
        try:
            query = AuditQuery.model_validate(dict(request.query_params))
            events = self._audit.list_for_device(
                authenticated.device.principal_id,
                authenticated.device.device_id,
                after_id=query.after_id,
                limit=query.limit,
            )
        except (ValidationError, ValueError):
            return JSONResponse({"error": "invalid_request"}, status_code=400)
        return JSONResponse(
            {
                "events": [
                    {
                        "event_id": event.event_id,
                        "event_type": event.event_type,
                        "occurred_at": event.occurred_at,
                        "remote_address_hash": event.remote_address_hash,
                        "detail_code": event.detail_code,
                    }
                    for event in events
                ]
            }
        )

    async def _device_push(self, request: Request) -> JSONResponse:
        authenticated = self._authorize(request, limit=30)
        if isinstance(authenticated, JSONResponse):
            return authenticated
        device = authenticated.device
        if request.method == "DELETE":
            await asyncio.to_thread(
                self._push_repository.unregister,
                device.principal_id,
                device.device_id,
            )
            self._record_audit(
                "push_unregistered",
                request=request,
                device_id=device.device_id,
                principal_id=device.principal_id,
            )
            return JSONResponse({"registered": False, "provider": ""})
        parsed = await self._parse_body(request, RegisterPushRequest)
        if isinstance(parsed, JSONResponse):
            return parsed
        try:
            registration = await asyncio.to_thread(
                self._push_repository.register,
                device.device_id,
                device.principal_id,
                parsed.provider,
                parsed.token,
            )
        except ValueError:
            return JSONResponse({"error": "invalid_request"}, status_code=400)
        self._record_audit(
            "push_registered",
            request=request,
            device_id=device.device_id,
            principal_id=device.principal_id,
            detail_code=registration.provider,
        )
        return JSONResponse(
            {"registered": True, "provider": registration.provider}
        )

    async def _resolve_approval(self, request: Request) -> JSONResponse:
        authenticated = self._authorize(request, limit=30)
        if isinstance(authenticated, JSONResponse):
            return authenticated
        approval_id = self._path_identifier(request, "approval_id")
        if approval_id is None:
            return JSONResponse({"error": "invalid_request"}, status_code=400)
        parsed = await self._parse_body(request, ResolveApprovalRequest)
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

    async def _events(self, request: Request) -> JSONResponse | StreamingResponse:
        authenticated = self._authorize(request, limit=20)
        if isinstance(authenticated, JSONResponse):
            return authenticated
        try:
            query = EventQuery.model_validate(dict(request.query_params))
            after_id = self._event_cursor(request, query.after_id)
        except (ValidationError, ValueError):
            return JSONResponse({"error": "invalid_request"}, status_code=400)
        device_id = authenticated.device.device_id
        if self._active_event_streams[device_id] >= 3:
            return JSONResponse({"error": "too_many_streams"}, status_code=429)
        self._active_event_streams[device_id] += 1
        self._record_audit(
            "stream_opened",
            request=request,
            device_id=device_id,
            principal_id=authenticated.device.principal_id,
        )
        token = self._bearer_token(request)
        principal_id = authenticated.device.principal_id

        async def stream():
            iterator = self._core.principal_task_events(
                principal_id,
                after_id=after_id,
            ).__aiter__()
            pending: asyncio.Task[Any] | None = None
            try:
                pending = asyncio.create_task(anext(iterator))
                while True:
                    done, _pending = await asyncio.wait(
                        {pending},
                        timeout=self._event_heartbeat_seconds,
                    )
                    if not done:
                        if self._authenticate_token(token) is None:
                            return
                        yield b": keepalive\n\n"
                        continue
                    try:
                        feed_event = pending.result()
                    except StopAsyncIteration:
                        return
                    except Exception:
                        logger.warning(
                            "Secure Gateway event stream lost",
                            exc_info=True,
                        )
                        yield self._sse("error", {"error": "unavailable"})
                        return
                    if self._authenticate_token(token) is None:
                        return
                    yield self._sse(
                        feed_event.event.event_type,
                        {
                            "feed_event_id": feed_event.feed_event_id,
                            "event": feed_event.event.model_dump(mode="json"),
                        },
                        event_id=feed_event.feed_event_id,
                    )
                    pending = asyncio.create_task(anext(iterator))
            finally:
                if pending is not None and not pending.done():
                    pending.cancel()
                    await asyncio.gather(pending, return_exceptions=True)
                close = getattr(iterator, "aclose", None)
                if close is not None:
                    await close()
                remaining = self._active_event_streams.get(device_id, 1) - 1
                if remaining > 0:
                    self._active_event_streams[device_id] = remaining
                else:
                    self._active_event_streams.pop(device_id, None)

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache, no-store",
                "X-Accel-Buffering": "no",
            },
        )

    async def _upload_artifact(self, request: Request) -> JSONResponse:
        authenticated = self._authorize(request, limit=20)
        if isinstance(authenticated, JSONResponse):
            return authenticated
        try:
            query = ArtifactUploadQuery.model_validate(dict(request.query_params))
        except ValidationError:
            return JSONResponse({"error": "invalid_request"}, status_code=400)
        media_type = request.headers.get("Content-Type", "").partition(";")[0].strip()
        if not self._valid_media_type(media_type):
            return JSONResponse({"error": "unsupported_media_type"}, status_code=415)
        declared_length = request.headers.get("Content-Length", "").strip()
        if declared_length:
            if not declared_length.isdecimal():
                return JSONResponse({"error": "invalid_request"}, status_code=400)
            if int(declared_length) > self._config.gateway_artifact_max_bytes:
                return JSONResponse({"error": "payload_too_large"}, status_code=413)
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > self._config.gateway_artifact_max_bytes:
                return JSONResponse({"error": "payload_too_large"}, status_code=413)
        if not body:
            return JSONResponse({"error": "invalid_request"}, status_code=400)
        token = self._bearer_token(request)
        renewed = self._authenticate_token(token)
        if renewed is None:
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        data_url = (
            f"data:{media_type};base64,"
            + base64.b64encode(body).decode("ascii")
        )
        try:
            artifact = await self._core.upload_artifact(
                renewed.device.principal_id,
                query.session_handle,
                data_url,
                media_type=media_type,
                name=query.name,
                caption=query.caption,
            )
        except Exception as exc:
            return self._core_error(exc)
        self._record_audit(
            "artifact_uploaded",
            request=request,
            device_id=renewed.device.device_id,
            principal_id=renewed.device.principal_id,
            detail_code=artifact.artifact_id,
        )
        return JSONResponse(
            {"artifact": artifact.model_dump(mode="json")},
            status_code=201,
        )

    async def _download_artifact(self, request: Request) -> JSONResponse | Response:
        authenticated = self._authorize(request, limit=60)
        if isinstance(authenticated, JSONResponse):
            return authenticated
        artifact_id = self._path_identifier(request, "artifact_id")
        if artifact_id is None:
            return JSONResponse({"error": "invalid_request"}, status_code=400)
        try:
            query = ArtifactDownloadQuery.model_validate(dict(request.query_params))
        except ValidationError:
            return JSONResponse({"error": "invalid_request"}, status_code=400)
        try:
            result = await self._core.download_artifact(
                authenticated.device.principal_id,
                query.session_handle,
                artifact_id,
            )
            data = self._decode_artifact_data_url(result.data_url)
        except ValueError:
            logger.warning("Secure Gateway received invalid Artifact data from Core")
            return JSONResponse({"error": "unavailable"}, status_code=503)
        except Exception as exc:
            return self._core_error(exc)
        if len(data) > self._config.gateway_artifact_max_bytes:
            return JSONResponse({"error": "payload_too_large"}, status_code=413)
        if self._authenticate_token(self._bearer_token(request)) is None:
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        return Response(
            data,
            media_type=result.artifact.media_type,
            headers={
                "Cache-Control": "no-store",
                "Content-Disposition": self._content_disposition(result.artifact.name),
                "X-Knoa-Artifact-Id": result.artifact.artifact_id,
            },
        )

    def _authorize(
        self,
        request: Request,
        *,
        limit: int,
    ) -> AuthenticatedGatewaySession | JSONResponse:
        token = self._bearer_token(request)
        if not token:
            self._record_audit("session_rejected", request=request)
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        session = self._authenticate_token(token)
        if session is None:
            self._record_audit("session_rejected", request=request)
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        key = f"authorized:{request.url.path}:{session.device.device_id}"
        if not self._limiter.allow(key, limit=limit):
            return JSONResponse({"error": "rate_limited"}, status_code=429)
        self._record_audit(
            "command",
            request=request,
            device_id=session.device.device_id,
            principal_id=session.device.principal_id,
            detail_code=f"{request.method} {request.url.path}",
        )
        return session

    def _record_audit(
        self,
        event_type: str,
        *,
        request: Request,
        device_id: str = "",
        principal_id: str = "",
        detail_code: str = "",
    ) -> None:
        try:
            remote = request.client.host if request.client is not None else ""
            self._audit.append(
                event_type,
                device_id=device_id,
                principal_id=principal_id,
                remote_address=remote,
                detail_code=detail_code,
            )
        except Exception:
            logger.warning("Secure Gateway audit append failed", exc_info=True)

    def _authenticate_token(self, token: str) -> AuthenticatedGatewaySession | None:
        try:
            return self._authentication.authenticate_session(token)
        except GatewayAuthenticationRejectedError:
            return None

    @staticmethod
    def _bearer_token(request: Request) -> str:
        authorization = request.headers.get("Authorization", "")
        scheme, space, token = authorization.partition(" ")
        if scheme.lower() != "bearer" or not space or not token or " " in token:
            return ""
        return token

    async def _body(
        self,
        request: Request,
        model: type[GatewayRequest],
        *,
        limit: int,
    ) -> GatewayRequest | JSONResponse:
        host = request.client.host if request.client is not None else "unknown"
        key = f"{request.url.path}:{host}"
        if not self._limiter.allow(key, limit=limit):
            return JSONResponse({"error": "rate_limited"}, status_code=429)
        return await self._parse_body(request, model)

    async def _parse_body(
        self,
        request: Request,
        model: type[GatewayRequest],
    ) -> GatewayRequest | JSONResponse:
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
                "artifact_not_found",
            }:
                return JSONResponse({"error": "not_found"}, status_code=404)
            if exc.code in {"invalid_request", "invalid_state"}:
                return JSONResponse({"error": "rejected"}, status_code=422)
            if exc.code == "artifact_too_large":
                return JSONResponse({"error": "payload_too_large"}, status_code=413)
        if isinstance(
            exc,
            (CoreConnectionLostError, CoreRequestTimeoutError),
        ):
            return JSONResponse({"error": "unavailable"}, status_code=503)
        logger.warning("Secure Gateway Core request failed", exc_info=exc)
        return JSONResponse({"error": "unavailable"}, status_code=503)

    @staticmethod
    def _event_cursor(request: Request, query_after_id: int) -> int:
        header = request.headers.get("Last-Event-ID", "").strip()
        if not header:
            return query_after_id
        if not header.isascii() or not header.isdecimal():
            raise ValueError("invalid event cursor")
        header_id = int(header)
        if header_id > 9_223_372_036_854_775_807:
            raise ValueError("invalid event cursor")
        if query_after_id and query_after_id != header_id:
            raise ValueError("conflicting event cursors")
        return header_id

    @staticmethod
    def _sse(event: str, payload: dict[str, Any], *, event_id: int | None = None) -> bytes:
        lines = []
        if event_id is not None:
            lines.append(f"id: {event_id}")
        lines.append(f"event: {event}")
        lines.append(
            "data: "
            + json.dumps(
                payload,
                ensure_ascii=False,
                separators=(",", ":"),
            )
        )
        return ("\n".join(lines) + "\n\n").encode("utf-8")

    @staticmethod
    def _valid_media_type(value: str) -> bool:
        return bool(
            0 < len(value) <= 128
            and re.fullmatch(r"[A-Za-z0-9!#$&^_.+-]+/[A-Za-z0-9!#$&^_.+-]+", value)
        )

    @staticmethod
    def _decode_artifact_data_url(data_url: str) -> bytes:
        if not data_url.startswith("data:") or ";base64," not in data_url:
            raise ValueError("invalid Artifact data URL")
        _metadata, encoded = data_url.split(",", 1)
        try:
            return base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise ValueError("invalid Artifact data URL") from exc

    @staticmethod
    def _content_disposition(name: str) -> str:
        from urllib.parse import quote

        encoded = quote(name or "artifact", safe="")
        return f"attachment; filename=artifact; filename*=UTF-8''{encoded}"

    @staticmethod
    def _tls_file(value: str, *, label: str, private: bool) -> Path:
        candidate = Path(value).expanduser()
        if not candidate.is_absolute():
            raise ValueError(f"Secure Gateway TLS {label} path must be absolute")
        try:
            metadata = candidate.lstat()
        except OSError as exc:
            raise ValueError(f"Secure Gateway TLS {label} is unavailable") from exc
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
            raise ValueError(
                f"Secure Gateway TLS {label} must be a regular non-symlink file"
            )
        if metadata.st_uid != os.geteuid():
            raise ValueError(f"Secure Gateway TLS {label} has the wrong owner")
        if private and stat.S_IMODE(metadata.st_mode) & 0o077:
            raise ValueError("Secure Gateway TLS private key must be owner-only")
        if metadata.st_size <= 0:
            raise ValueError(f"Secure Gateway TLS {label} is empty")
        return candidate.resolve()

    @staticmethod
    def _challenge_response(challenge: Any) -> JSONResponse:
        return JSONResponse(
            {
                "challenge_id": challenge.challenge_id,
                "nonce": challenge.nonce,
                "expires_at": challenge.expires_at,
            }
        )
