"""Fail-closed HTTP/TLS surface for Secure Gateway mobile access."""
from __future__ import annotations

import asyncio
import contextlib
import logging
import secrets as token_secrets
import time
from collections import defaultdict, deque
from pathlib import Path

import uvicorn
from starlette.applications import Starlette
from starlette.routing import Route

from knoa_platform.config import AppConfig
from knoa_platform.extensions.import_service import ExtensionImportService
from knoa_platform.extensions.package_store import PackageStore
from knoa_platform.fleet import FleetCandidateService
from knoa_platform.gateway.audit import GatewayAuditRepository
from knoa_platform.gateway.auth import (
    GatewayAuthenticationService,
    GatewayAuthRepository,
)
from knoa_platform.gateway.core import GatewayCoreBridge
from knoa_platform.gateway.http import GatewayHttp
from knoa_platform.gateway.identity import (
    GatewayIdentityRepository,
)
from knoa_platform.gateway.routes import (
    ArtifactRoutes,
    ConfigurationRoutes,
    ConsoleRoutes,
    ConversationRoutes,
    DeviceRoutes,
    ExtensionRoutes,
    FleetRoutes,
    RemoteResourceRoutes,
    SecretRoutes,
    TaskRoutes,
)
from knoa_platform.gateway.streaming import GatewayStreaming
from knoa_platform.mobile_releases import AndroidReleaseRepository
from knoa_platform.network_tls import is_loopback_host
from knoa_platform.node_hub import (
    NodeHubRoutes,
    NodeHubService,
    NodeHubStore,
    NodeRelayManager,
)
from knoa_platform.node_identity import NodeIdentityStore
from knoa_platform.remote_models import (
    RemoteModelEndpoint,
    RemoteModelInvocationRepository,
)
from knoa_platform.runtime import RuntimePaths
from knoa_platform.secrets import SecretStore

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


class SecureGatewayAdapter(
    ConsoleRoutes,
    ConversationRoutes,
    TaskRoutes,
    ArtifactRoutes,
    DeviceRoutes,
    ConfigurationRoutes,
    ExtensionRoutes,
    FleetRoutes,
    SecretRoutes,
    RemoteResourceRoutes,
    NodeHubRoutes,
    GatewayStreaming,
    GatewayHttp,
):
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
        paths = RuntimePaths.from_root(config.runtime_root)
        database = paths.data / "gateway.db"
        identities = GatewayIdentityRepository(database)
        self._identities = identities
        if authentication is None:
            authentication = GatewayAuthenticationService(
                identities,
                GatewayAuthRepository(database),
            )
        self._authentication = authentication
        self._audit = audit or GatewayAuditRepository(database)
        self._core = core or GatewayCoreBridge(config)
        self._node_identity = NodeIdentityStore(
            paths.data / "node-identity.json"
        ).load_or_create()
        self._node_hub_store = NodeHubStore(paths.data / "node-hub.json")
        self._remote_models = RemoteModelEndpoint(
            RemoteModelInvocationRepository(
                paths.data / "remote-model-invocations.db"
            ),
            core=self._core,
            bootstrap=config,
            paths=paths,
            identity=self._node_identity,
            hub_store=self._node_hub_store,
        )
        self._extension_imports = ExtensionImportService(
            PackageStore(paths.packages),
            self._core,
        )
        self._fleet_candidates = FleetCandidateService(
            self._node_identity,
            identities,
            self._core,
        )
        self._provider_secrets = SecretStore(paths.secrets / "providers")
        self._releases = release_repository or AndroidReleaseRepository(
            RuntimePaths.from_root(config.runtime_root).data
            / "mobile-releases"
            / "android"
        )
        self._limiter = limiter or _WindowLimiter()
        self._event_heartbeat_seconds = max(0.01, event_heartbeat_seconds)
        self._console_csrf_token = token_secrets.token_urlsafe(32)
        self._active_event_streams: dict[str, int] = defaultdict(int)
        self._stream_replacements: dict[tuple[str, str], asyncio.Event] = {}
        self._server: _EmbeddedUvicornServer | None = None
        self._server_task: asyncio.Task[None] | None = None
        self.app = Starlette(
            routes=[
                Route("/console", self._console_page, methods=["GET"]),
                Route("/v1/console/status", self._console_status, methods=["GET"]),
                Route(
                    "/v1/console/hub/enroll",
                    self._console_hub_enroll,
                    methods=["POST"],
                ),
                Route(
                    "/v1/console/pairing",
                    self._console_pairing,
                    methods=["POST"],
                ),
                Route("/health", self._health, methods=["GET"]),
                Route("/openapi.json", self._openapi, methods=["GET"]),
                Route("/v1/pair/challenge", self._pair_challenge, methods=["POST"]),
                Route("/v1/pair/complete", self._pair_complete, methods=["POST"]),
                Route("/v1/auth/challenge", self._auth_challenge, methods=["POST"]),
                Route("/v1/auth/complete", self._auth_complete, methods=["POST"]),
                Route("/v1/session", self._session, methods=["GET"]),
                Route("/v1/node", self._node, methods=["GET"]),
                Route("/v1/hub", self._hub_status, methods=["GET"]),
                Route("/v1/hub/enroll", self._hub_enroll, methods=["POST"]),
                Route("/v1/hub", self._hub_remove, methods=["DELETE"]),
                Route(
                    "/v1/resource-invocations/{invocation_id:str}",
                    self._resource_invocation,
                    methods=["POST", "DELETE"],
                ),
                Route("/v1/agents", self._agents, methods=["GET"]),
                Route("/v1/extensions/packages", self._extension_packages, methods=["GET"]),
                Route("/v1/extensions/import/skill", self._extension_import_skill, methods=["POST"]),
                Route("/v1/extensions/import/mcp/local", self._extension_import_local_mcp, methods=["POST"]),
                Route("/v1/extensions/import/mcp/remote", self._extension_import_remote_mcp, methods=["POST"]),
                Route("/v1/fleet/candidates/apply", self._fleet_apply, methods=["POST"]),
                Route("/v1/secrets/{reference:str}", self._secret, methods=["GET", "PUT"]),
                Route("/v1/config/current", self._config_current, methods=["GET"]),
                Route("/v1/config/drafts", self._config_drafts, methods=["POST"]),
                Route(
                    "/v1/config/drafts/{draft_id:str}",
                    self._config_draft,
                    methods=["GET", "PUT"],
                ),
                Route(
                    "/v1/config/drafts/{draft_id:str}/validate",
                    self._config_validate,
                    methods=["POST"],
                ),
                Route(
                    "/v1/config/drafts/{draft_id:str}/preflight",
                    self._config_preflight,
                    methods=["POST"],
                ),
                Route(
                    "/v1/config/drafts/{draft_id:str}/publish",
                    self._config_publish,
                    methods=["POST"],
                ),
                Route("/v1/config/diff", self._config_diff, methods=["GET"]),
                Route(
                    "/v1/config/policy-preview",
                    self._config_policy_preview,
                    methods=["POST"],
                ),
                Route("/v1/mcp/resources", self._list_mcp_resources, methods=["GET"]),
                Route("/v1/sessions", self._create_session, methods=["POST"]),
                Route("/v1/conversations/sessions", self._list_conversation_sessions, methods=["GET"]),
                Route(
                    "/v1/conversations/sessions/{session_handle:str}",
                    self._conversation_session,
                    methods=["GET", "PATCH", "DELETE"],
                ),
                Route(
                    "/v1/conversations/sessions/{session_handle:str}/turns",
                    self._create_chat_turn,
                    methods=["POST"],
                ),
                Route(
                    "/v1/conversations/sessions/{session_handle:str}/turns",
                    self._list_chat_turns,
                    methods=["GET"],
                ),
                Route(
                    "/v1/conversations/turns/{turn_id:str}",
                    self._get_chat_turn,
                    methods=["GET"],
                ),
                Route(
                    "/v1/conversations/turns/{turn_id:str}/stream",
                    self._chat_turn_stream,
                    methods=["GET"],
                ),
                Route(
                    "/v1/conversations/turns/{turn_id:str}/cancel",
                    self._cancel_chat_turn,
                    methods=["POST"],
                ),
                Route(
                    "/v1/conversations/turns/{turn_id:str}/retry",
                    self._retry_chat_turn,
                    methods=["POST"],
                ),
                Route(
                    "/v1/conversations/approvals/{approval_id:str}/resolve",
                    self._resolve_chat_approval,
                    methods=["POST"],
                ),
                Route(
                    "/v1/interactions/{interaction_id:str}/resolve",
                    self._resolve_interaction,
                    methods=["POST"],
                ),
                Route("/v1/tasks", self._create_task, methods=["POST"]),
                Route("/v1/tasks", self._list_tasks, methods=["GET"]),
                Route("/v1/events", self._events, methods=["GET"]),
                Route("/v1/events/poll", self._events_poll, methods=["GET"]),
                Route("/v1/artifacts", self._upload_artifact, methods=["POST"]),
                Route(
                    "/v1/artifacts/{artifact_id:str}",
                    self._download_artifact,
                    methods=["GET"],
                ),
                Route("/v1/tasks/{task_id:str}", self._get_task, methods=["GET"]),
                Route("/v1/tasks/{task_id:str}", self._update_task, methods=["PATCH"]),
                Route("/v1/tasks/{task_id:str}", self._delete_task, methods=["DELETE"]),
                Route(
                    "/v1/tasks/{task_id:str}/execute",
                    self._execute_task,
                    methods=["POST"],
                ),
                Route(
                    "/v1/tasks/{task_id:str}/continue",
                    self._continue_task,
                    methods=["POST"],
                ),
                Route(
                    "/v1/tasks/{task_id:str}/executions",
                    self._list_task_executions,
                    methods=["GET"],
                ),
                Route(
                    "/v1/tasks/{task_id:str}/pause",
                    self._pause_task_definition,
                    methods=["POST"],
                ),
                Route(
                    "/v1/tasks/{task_id:str}/resume",
                    self._resume_task_definition,
                    methods=["POST"],
                ),
                Route(
                    "/v1/tasks/{task_id:str}/archive",
                    self._archive_task,
                    methods=["POST"],
                ),
                Route(
                    "/v1/tasks/{task_id:str}/restore",
                    self._restore_task,
                    methods=["POST"],
                ),
                Route(
                    "/v1/task-executions/{execution_id:str}",
                    self._get_task_execution,
                    methods=["GET"],
                ),
                Route(
                    "/v1/task-executions/{execution_id:str}",
                    self._delete_task_execution,
                    methods=["DELETE"],
                ),
                Route(
                    "/v1/task-executions/{execution_id:str}/events",
                    self._task_execution_events,
                    methods=["GET"],
                ),
                Route(
                    "/v1/task-executions/{execution_id:str}/cancel",
                    self._cancel_task_execution,
                    methods=["POST"],
                ),
                Route(
                    "/v1/task-executions/{execution_id:str}/pause",
                    self._pause_task_execution,
                    methods=["POST"],
                ),
                Route(
                    "/v1/task-executions/{execution_id:str}/resume",
                    self._resume_task_execution,
                    methods=["POST"],
                ),
                Route(
                    "/v1/task-executions/{execution_id:str}/rerun",
                    self._rerun_task_execution,
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
                    "/v1/mcp/resources",
                    self._list_mcp_resources,
                    methods=["GET"],
                ),
                Route(
                    "/v1/mobile/releases/android/latest",
                    self._latest_android_release,
                    methods=["GET"],
                ),
                Route(
                    "/releases/android/{version_code:str}/{sha256:str}/knoa.apk",
                    self._download_android_release,
                    methods=["GET"],
                ),
                Route("/v1/device/audit", self._device_audit, methods=["GET"]),
                Route("/v1/device", self._device, methods=["DELETE"]),
                Route(
                    "/v1/approvals/{approval_id:str}/resolve",
                    self._resolve_approval,
                    methods=["POST"],
                ),
            ]
        )
        self._node_hub = NodeHubService(
            self._node_hub_store,
            self._node_identity,
        )
        self._node_relay = NodeRelayManager(
            store=self._node_hub.store,
            identity=self._node_identity,
            identities=identities,
            app=self.app,
            core=self._core,
            remote_models=self._remote_models,
            direct_gateway_url=config.gateway_public_url,
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
                    await self._node_relay.start()
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
        await self._node_relay.stop()
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
