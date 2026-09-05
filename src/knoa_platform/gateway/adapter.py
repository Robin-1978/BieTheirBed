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
from knoa_platform.database_maintenance import maintain_sqlite_database
from knoa_platform.extensions.import_service import ExtensionImportService
from knoa_platform.extensions.capability_bundle import (
    CapabilityInstallationRepository,
    CapabilityInstaller,
)
from knoa_platform.extensions.package_store import PackageStore
from knoa_platform.extensions.capability_catalog import (
    CapabilityCatalogService,
    OFFICIAL_CATALOG_TRUST_ROOTS,
)
from knoa_platform.events import EventSourceRepository
from knoa_platform.improvement import ImprovementService
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
    ChannelRoutes,
    ConfigurationRoutes,
    ConsoleRoutes,
    ConversationRoutes,
    DeviceRoutes,
    ExtensionRoutes,
    EventSourceRoutes,
    GovernanceRoutes,
    FleetRoutes,
    MemoriesRouteMixin,
    P2PRoutes,
    RemoteResourceRoutes,
    SecretRoutes,
    TaskRoutes,
)
from knoa_platform.gateway.streaming import GatewayStreaming
from knoa_platform.host_lifecycle_client import HostLifecycleClient
from knoa_platform.mobile_releases import AndroidReleaseRepository
from knoa_platform.mdns import MdnsPublisher
from knoa_platform.network_tls import is_loopback_host
from knoa_platform.p2p import P2PServer
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
from knoa_platform.transport_health import TransportHealth
from knoa_platform.transport_middleware import TransportHealthMiddleware

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
    ChannelRoutes,
    ConversationRoutes,
    TaskRoutes,
    ArtifactRoutes,
    DeviceRoutes,
    ConfigurationRoutes,
    ExtensionRoutes,
    EventSourceRoutes,
    GovernanceRoutes,
    FleetRoutes,
    MemoriesRouteMixin,
    P2PRoutes,
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
        channel_controller: object | None = None,
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
        self._channel_controller = channel_controller
        self._transport_health = TransportHealth()
        paths = RuntimePaths.from_root(config.runtime_root)
        database = paths.data / "gateway.db"
        identities = GatewayIdentityRepository(database)
        self._identities = identities
        self._auth_repository: GatewayAuthRepository | None = None
        if authentication is None:
            self._auth_repository = GatewayAuthRepository(database)
            authentication = GatewayAuthenticationService(
                identities,
                self._auth_repository,
            )
        self._authentication = authentication
        self._audit = audit or GatewayAuditRepository(database)
        self._database = database
        self._maintenance_interval = max(
            60,
            config.attachment_cleanup_interval_seconds,
        )
        self._maintenance_task: asyncio.Task[None] | None = None
        self._core = core or GatewayCoreBridge(config)
        self._node_identity = NodeIdentityStore(
            paths.data / "node-identity.json"
        ).load_or_create()
        self._node_hub_store = NodeHubStore(paths.data / "node-hub.json")
        self._remote_models = RemoteModelEndpoint(
            RemoteModelInvocationRepository(paths.data / "remote-model-invocations.db"),
            core=self._core,
            bootstrap=config,
            paths=paths,
            identity=self._node_identity,
            hub_store=self._node_hub_store,
        )
        package_store = PackageStore(paths.packages)
        self._extension_imports = ExtensionImportService(
            package_store,
            self._core,
        )
        self._capability_installer = CapabilityInstaller(
            package_store,
            self._core,
            CapabilityInstallationRepository(database),
            inspector=self._extension_imports,
        )
        self._event_sources = EventSourceRepository(database)
        self._improvements = ImprovementService(database)
        repository_root = Path(__file__).resolve().parents[3]
        catalog_path = repository_root / "catalog" / "capabilities.json"
        catalog_source_root = repository_root
        if not catalog_path.is_file():
            catalog_source_root = Path(__file__).resolve().parents[1] / "resources"
            catalog_path = catalog_source_root / "capability_catalog.json"
        self._capability_catalog = CapabilityCatalogService(
            catalog_path,
            trust_roots=OFFICIAL_CATALOG_TRUST_ROOTS,
            source_root=catalog_source_root,
            database=database,
            packages=package_store,
            installer=self._capability_installer,
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
        self._lan_server: _EmbeddedUvicornServer | None = None
        self._lan_server_task: asyncio.Task[None] | None = None
        self._mdns: MdnsPublisher | None = None
        self._console_csrf_token = token_secrets.token_urlsafe(32)
        self._host_lifecycle = HostLifecycleClient.from_environment()
        self._active_event_streams: dict[str, int] = defaultdict(int)
        self._stream_replacements: dict[tuple[str, str], asyncio.Event] = {}
        self._server: _EmbeddedUvicornServer | None = None
        self._server_task: asyncio.Task[None] | None = None
        self.app = Starlette(
            routes=[
                Route("/console", self._console_page, methods=["GET"]),
                Route("/v1/console/status", self._console_status, methods=["GET"]),
                Route(
                    "/v1/console/diagnostics",
                    self._console_diagnostics,
                    methods=["GET"],
                ),
                Route(
                    "/v1/console/diagnostics/repair",
                    self._console_diagnostic_repair,
                    methods=["POST"],
                ),
                Route(
                    "/v1/console/hub/enroll",
                    self._console_hub_enroll,
                    methods=["POST"],
                ),
                Route(
                    "/v1/console/node",
                    self._console_node_profile,
                    methods=["PUT"],
                ),
                Route(
                    "/v1/console/pairing",
                    self._console_pairing,
                    methods=["POST"],
                ),
                Route(
                    "/v1/console/lifecycle",
                    self._console_lifecycle,
                    methods=["GET"],
                ),
                Route(
                    "/v1/console/lifecycle/actions",
                    self._console_lifecycle_action,
                    methods=["POST"],
                ),
                Route(
                    "/v1/console/lifecycle/bundles/{name:str}",
                    self._console_lifecycle_bundle,
                    methods=["PUT"],
                ),
                Route(
                    "/v1/console/config",
                    self._console_config,
                    methods=["GET"],
                ),
                Route(
                    "/v1/console/config/publish",
                    self._console_config_publish,
                    methods=["POST"],
                ),
                Route(
                    "/v1/console/extensions",
                    self._console_extensions,
                    methods=["GET"],
                ),
                Route(
                    "/v1/console/capabilities/{capability_id:str}/prepare",
                    self._console_capability_prepare,
                    methods=["POST"],
                ),
                Route(
                    "/v1/console/capabilities/confirm",
                    self._console_capability_confirm,
                    methods=["POST"],
                ),
                Route(
                    "/v1/console/capabilities/{capability_id:str}/state",
                    self._console_capability_state,
                    methods=["PATCH"],
                ),
                Route(
                    "/v1/console/capabilities/{capability_id:str}/rollback",
                    self._console_capability_rollback,
                    methods=["POST"],
                ),
                Route(
                    "/v1/console/workspace-resources",
                    self._console_workspace_resources,
                    methods=["GET"],
                ),
                Route(
                    "/v1/console/secrets/{reference:str}",
                    self._console_secret,
                    methods=["GET", "PUT"],
                ),
                Route("/health", self._health, methods=["GET"]),
                Route("/openapi.json", self._openapi, methods=["GET"]),
                Route("/v1/pair/challenge", self._pair_challenge, methods=["POST"]),
                Route("/v1/pair/complete", self._pair_complete, methods=["POST"]),
                Route("/v1/auth/challenge", self._auth_challenge, methods=["POST"]),
                Route("/v1/auth/complete", self._auth_complete, methods=["POST"]),
                Route("/v1/p2p/offer", self._p2p_offer, methods=["POST"]),
                Route(
                    "/v1/resource-p2p/offer",
                    self._resource_p2p_offer,
                    methods=["POST"],
                ),
                Route("/v1/session", self._session, methods=["GET"]),
                Route("/v1/node", self._node, methods=["GET", "PUT"]),
                Route(
                    "/v1/channels/dingtalk",
                    self._dingtalk_channel,
                    methods=["GET", "PUT"],
                ),
                Route("/v1/hub", self._hub_status, methods=["GET"]),
                Route("/v1/hub/enroll", self._hub_enroll, methods=["POST"]),
                Route("/v1/hub", self._hub_remove, methods=["DELETE"]),
                Route(
                    "/v1/resource-invocations/{invocation_id:str}",
                    self._resource_invocation,
                    methods=["POST", "DELETE"],
                ),
                Route("/v1/agents", self._agents, methods=["GET"]),
                Route(
                    "/v1/agents/availability", self._agent_availability, methods=["GET"]
                ),
                Route(
                    "/v1/extensions/packages", self._extension_packages, methods=["GET"]
                ),
                Route(
                    "/v1/extensions/import/skill",
                    self._extension_import_skill,
                    methods=["POST"],
                ),
                Route(
                    "/v1/extensions/import/mcp/local",
                    self._extension_import_local_mcp,
                    methods=["POST"],
                ),
                Route(
                    "/v1/extensions/import/mcp/remote",
                    self._extension_import_remote_mcp,
                    methods=["POST"],
                ),
                Route(
                    "/v1/capabilities/installations",
                    self._capability_installations,
                    methods=["GET"],
                ),
                Route(
                    "/v1/capabilities/prepare",
                    self._capability_prepare,
                    methods=["POST"],
                ),
                Route(
                    "/v1/capabilities/confirm",
                    self._capability_confirm,
                    methods=["POST"],
                ),
                Route("/v1/capability-catalog", self._catalog_entries, methods=["GET"]),
                Route(
                    "/v1/capability-catalog/{capability_id:str}/selection",
                    self._catalog_select,
                    methods=["PUT"],
                ),
                Route(
                    "/v1/capability-catalog/{capability_id:str}/prepare",
                    self._catalog_prepare,
                    methods=["POST"],
                ),
                Route(
                    "/v1/capabilities/{capability_id:str}/state",
                    self._capability_state,
                    methods=["PATCH"],
                ),
                Route(
                    "/v1/capabilities/{capability_id:str}/rollback",
                    self._capability_rollback,
                    methods=["POST"],
                ),
                Route(
                    "/v1/fleet/candidates/apply", self._fleet_apply, methods=["POST"]
                ),
                Route(
                    "/v1/improvements/evidence",
                    self._improvement_evidence,
                    methods=["POST"],
                ),
                Route(
                    "/v1/improvements/cases", self._improvement_case, methods=["POST"]
                ),
                Route(
                    "/v1/improvements/candidates",
                    self._improvement_candidates,
                    methods=["GET"],
                ),
                Route(
                    "/v1/improvements/candidates",
                    self._improvement_candidate,
                    methods=["POST"],
                ),
                Route(
                    "/v1/improvements/candidates/{candidate_id:str}/replay",
                    self._improvement_replay,
                    methods=["POST"],
                ),
                Route(
                    "/v1/improvements/candidates/{candidate_id:str}/approve",
                    self._improvement_approve,
                    methods=["POST"],
                ),
                Route(
                    "/v1/improvements/candidates/{candidate_id:str}/canary",
                    self._improvement_finish,
                    methods=["POST"],
                ),
                Route(
                    "/v1/improvements/candidates/{candidate_id:str}/rollback",
                    self._improvement_rollback,
                    methods=["POST"],
                ),
                Route(
                    "/v1/secrets/{reference:str}", self._secret, methods=["GET", "PUT"]
                ),
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
                Route(
                    "/v1/conversations/sessions",
                    self._list_conversation_sessions,
                    methods=["GET"],
                ),
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
                Route("/v1/memories", self._list_memories, methods=["GET"]),
                Route("/v1/memories/clear", self._clear_memories, methods=["POST"]),
                Route("/v1/event-sources", self._list_event_sources, methods=["GET"]),
                Route("/v1/event-sources", self._create_event_source, methods=["POST"]),
                Route(
                    "/v1/event-sources/{source_id:str}",
                    self._get_event_source,
                    methods=["GET"],
                ),
                Route(
                    "/v1/event-sources/{source_id:str}",
                    self._delete_event_source,
                    methods=["DELETE"],
                ),
                Route(
                    "/v1/event-sources/{source_id:str}/state",
                    self._set_event_source_state,
                    methods=["PATCH"],
                ),
                Route(
                    "/v1/event-sources/{source_id:str}/test",
                    self._test_event_source,
                    methods=["POST"],
                ),
                Route(
                    "/v1/event-sources/{source_id:str}/rotate-secret",
                    self._rotate_event_source_secret,
                    methods=["POST"],
                ),
                Route(
                    "/v1/event-sources/{source_id:str}/events",
                    self._event_source_events,
                    methods=["GET"],
                ),
                Route("/v1/events", self._events, methods=["GET"]),
                Route("/v1/events/poll", self._events_poll, methods=["GET"]),
                Route("/v1/artifacts", self._search_artifacts, methods=["GET"]),
                Route("/v1/artifacts", self._upload_artifact, methods=["POST"]),
                Route(
                    "/v1/artifacts/{artifact_id:str}",
                    self._download_artifact,
                    methods=["GET"],
                ),
                Route("/v1/tasks/{task_id:str}", self._get_task, methods=["GET"]),
                Route(
                    "/v1/tasks/{task_id:str}/glance",
                    self._get_task_glance,
                    methods=["GET"],
                ),
                Route(
                    "/v1/tasks/{task_id:str}/preflight",
                    self._preflight_task,
                    methods=["GET"],
                ),
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
        self.app.add_middleware(
            TransportHealthMiddleware,
            health=self._transport_health,
        )
        self._p2p = P2PServer(self.app)
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
            owner_principal_id=config.owner_principal_id,
        )

    @property
    def bound_port(self) -> int | None:
        if self._server is None or not self._server.servers:
            return None
        sockets = self._server.servers[0].sockets
        return int(sockets[0].getsockname()[1]) if sockets else None

    @property
    def lan_bound_port(self) -> int | None:
        if self._lan_server is None or not self._lan_server.servers:
            return None
        sockets = self._lan_server.servers[0].sockets
        return int(sockets[0].getsockname()[1]) if sockets else None

    async def start(self) -> None:
        if self._server_task is not None:
            raise RuntimeError("SecureGatewayAdapter is already started")
        await self._run_database_maintenance()
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
                    self._maintenance_task = asyncio.create_task(
                        self._maintenance_loop(),
                        name="knoa-gateway-database-maintenance",
                    )
                    if self._config.gateway_lan_enabled:
                        try:
                            lan_server = _EmbeddedUvicornServer(
                                uvicorn.Config(
                                    self.app,
                                    host=self._config.gateway_lan_host,
                                    port=self._config.gateway_lan_port,
                                    log_config=None,
                                    access_log=False,
                                    lifespan="off",
                                )
                            )
                            lan_task = asyncio.create_task(
                                lan_server.serve(), name="knoa-lan-gateway"
                            )
                            self._lan_server, self._lan_server_task = (
                                lan_server,
                                lan_task,
                            )
                            for _ in range(500):
                                if lan_server.started:
                                    break
                                if lan_task.done():
                                    await lan_task
                                    raise RuntimeError(
                                        "LAN Gateway stopped during startup"
                                    )
                                await asyncio.sleep(0.01)
                            if not lan_server.started:
                                raise TimeoutError("LAN Gateway startup timed out")
                            from knoa_platform import __version__

                            self._mdns = MdnsPublisher(
                                node_id=self._node_identity.node_id,
                                port=self.lan_bound_port
                                or self._config.gateway_lan_port,
                                version=__version__,
                                signing_public_key=self._node_identity.signing_public_key,
                            )
                            await self._mdns.start()
                        except Exception as exc:  # noqa: BLE001
                            logger.warning("LAN Gateway/mDNS unavailable: %s", exc)
                            lan_server = self._lan_server
                            lan_task = self._lan_server_task
                            self._lan_server, self._lan_server_task = None, None
                            if lan_server is not None:
                                lan_server.should_exit = True
                            if lan_task is not None:
                                lan_task.cancel()
                                await asyncio.gather(lan_task, return_exceptions=True)
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
        maintenance, self._maintenance_task = self._maintenance_task, None
        if maintenance is not None:
            maintenance.cancel()
            await asyncio.gather(maintenance, return_exceptions=True)
        if self._mdns is not None:
            await self._mdns.stop()
            self._mdns = None
        await self._node_relay.stop()
        await self._p2p.close()
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
        lan_server, self._lan_server = self._lan_server, None
        lan_task, self._lan_server_task = self._lan_server_task, None
        if lan_server is not None:
            lan_server.should_exit = True
        if lan_task is not None:
            try:
                await asyncio.wait_for(lan_task, timeout=5.0)
            except TimeoutError:
                if lan_server is not None:
                    lan_server.force_exit = True
                lan_task.cancel()
                await asyncio.gather(lan_task, return_exceptions=True)
        await self._core.close()

    async def _maintenance_loop(self) -> None:
        while True:
            await asyncio.sleep(self._maintenance_interval)
            await self._run_database_maintenance()

    async def _run_database_maintenance(self) -> None:
        try:
            await asyncio.to_thread(self._maintain_database)
        except Exception:
            logger.warning("Gateway database maintenance failed", exc_info=True)

    def _maintain_database(self) -> None:
        pruned = self._audit.prune()
        if self._auth_repository is not None:
            pruned += self._auth_repository.cleanup_expired()
        maintain_sqlite_database(self._database)
        if pruned:
            logger.info("Pruned %d stale Gateway database records", pruned)
