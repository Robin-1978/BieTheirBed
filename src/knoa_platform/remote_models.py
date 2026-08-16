"""Workspace-scoped remote model deployment, execution and Provider adapter."""

from __future__ import annotations

import asyncio
import hashlib
import json
import secrets
import time
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import quote, urlsplit, urlunsplit

import httpx
import websockets

from knoa_platform.agent_runtime.contracts import HealthStatus
from knoa_platform.agent_runtime.http_provider import HttpModelProvider
from knoa_platform.agent_runtime.model_step import (
    ModelProviderPort,
    ProviderCallRequest,
    ProviderChunk,
)
from knoa_platform.config import AppConfig, ResolvedModelConfig, ThinkingConfig
from knoa_platform.configuration.models import ManagedConfig
from knoa_platform.hub.relay import RelayFrame
from knoa_platform.node_identity import NodeIdentity, NodeIdentityStore
from knoa_platform.relay_protocol import (
    canonical_json,
    decode_base64url,
    encode_base64url,
)
from knoa_platform.resource_protocol import (
    ResourceServerHello,
    create_resource_client_hello,
    finish_resource_client_handshake,
    verify_resource_ticket,
)
from knoa_platform.runtime import RuntimePaths
from knoa_platform.secrets import SecretStore
from knoa_platform.sqlite_connection import connect_sqlite, initialize_wal

if TYPE_CHECKING:
    from knoa_platform.node_hub import NodeHubEnrollment, NodeHubStore

_MAX_RESPONSE_BYTES = 32 * 1024 * 1024
_RELAY_BODY_CHUNK = 192 * 1024


def materialized_deployment_digest(
    managed: ManagedConfig,
    deployment_id: str,
) -> str:
    deployment = managed.model_deployments[deployment_id]
    model = managed.models[deployment.model_alias]
    provider = managed.providers[model.provider]
    material = {
        "deployment_id": deployment_id,
        "deployment": deployment.model_dump(mode="json"),
        "model": model.model_dump(mode="json"),
        "provider": provider.model_dump(
            mode="json",
            exclude={"api_key_ref", "api_key_env"},
        ),
    }
    return hashlib.sha256(canonical_json(material)).hexdigest()


class RemoteModelInvocationRepository:
    """Durable single-execution identity and replayable ProviderChunk log."""

    def __init__(self, path: str | Path, *, clock=time.time) -> None:
        self.path = Path(path).expanduser().resolve()
        self._clock = clock
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        initialize_wal(self.path)
        with self._connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS remote_model_invocations(
                    invocation_id TEXT PRIMARY KEY,
                    request_digest TEXT NOT NULL,
                    deployment_id TEXT NOT NULL,
                    materialized_digest TEXT NOT NULL,
                    execution_epoch TEXT NOT NULL,
                    state TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS remote_model_invocation_events(
                    invocation_id TEXT NOT NULL,
                    sequence INTEGER NOT NULL,
                    chunk_json TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    PRIMARY KEY(invocation_id, sequence)
                );
                """
            )
            rows = db.execute(
                "SELECT invocation_id FROM remote_model_invocations WHERE state='running'"
            ).fetchall()
            for row in rows:
                invocation_id = str(row["invocation_id"])
                sequence = int(
                    db.execute(
                        """SELECT COALESCE(MAX(sequence), -1) + 1 AS next_sequence
                           FROM remote_model_invocation_events WHERE invocation_id=?""",
                        (invocation_id,),
                    ).fetchone()["next_sequence"]
                )
                chunk = ProviderChunk(
                    finish_reason="error",
                    terminal=True,
                    error_code="outcome_unknown",
                )
                now = self._clock()
                db.execute(
                    "INSERT INTO remote_model_invocation_events VALUES (?, ?, ?, ?)",
                    (invocation_id, sequence, chunk.model_dump_json(), now),
                )
                db.execute(
                    """UPDATE remote_model_invocations
                       SET state='outcome_unknown', updated_at=? WHERE invocation_id=?""",
                    (now, invocation_id),
                )

    def _connect(self):
        return connect_sqlite(self.path, foreign_keys=True)

    def admit(
        self,
        invocation_id: str,
        *,
        request_digest: str,
        deployment_id: str,
        materialized_digest: str,
    ) -> tuple[dict, bool]:
        now = self._clock()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM remote_model_invocations WHERE invocation_id=?",
                (invocation_id,),
            ).fetchone()
            if row is None:
                execution_epoch = f"epoch_{secrets.token_urlsafe(18)}"
                db.execute(
                    "INSERT INTO remote_model_invocations VALUES (?, ?, ?, ?, ?, 'running', ?, ?)",
                    (
                        invocation_id,
                        request_digest,
                        deployment_id,
                        materialized_digest,
                        execution_epoch,
                        now,
                        now,
                    ),
                )
                row = db.execute(
                    "SELECT * FROM remote_model_invocations WHERE invocation_id=?",
                    (invocation_id,),
                ).fetchone()
                return dict(row), True
        item = dict(row)
        if (
            item["request_digest"] != request_digest
            or item["deployment_id"] != deployment_id
            or item["materialized_digest"] != materialized_digest
        ):
            raise PermissionError("Invocation identity was reused with different material")
        return item, False

    def append(self, invocation_id: str, chunk: ProviderChunk) -> None:
        now = self._clock()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT state FROM remote_model_invocations WHERE invocation_id=?",
                (invocation_id,),
            ).fetchone()
            if row is None:
                raise LookupError("Remote model invocation not found")
            if str(row["state"]) != "running":
                return
            sequence = int(
                db.execute(
                    """SELECT COALESCE(MAX(sequence), -1) + 1 AS next_sequence
                       FROM remote_model_invocation_events WHERE invocation_id=?""",
                    (invocation_id,),
                ).fetchone()["next_sequence"]
            )
            db.execute(
                "INSERT INTO remote_model_invocation_events VALUES (?, ?, ?, ?)",
                (invocation_id, sequence, chunk.model_dump_json(), now),
            )
            if chunk.terminal:
                state = (
                    "completed"
                    if chunk.finish_reason != "error"
                    else chunk.error_code or "failed"
                )
                db.execute(
                    """UPDATE remote_model_invocations SET state=?, updated_at=?
                       WHERE invocation_id=?""",
                    (state, now, invocation_id),
                )

    def cancel(self, invocation_id: str) -> bool:
        with self._connect() as db:
            row = db.execute(
                "SELECT state FROM remote_model_invocations WHERE invocation_id=?",
                (invocation_id,),
            ).fetchone()
        return row is not None and str(row["state"]) == "running"

    def chunks(self, invocation_id: str) -> tuple[ProviderChunk, ...]:
        with self._connect() as db:
            rows = db.execute(
                """SELECT chunk_json FROM remote_model_invocation_events
                   WHERE invocation_id=? ORDER BY sequence""",
                (invocation_id,),
            ).fetchall()
        return tuple(ProviderChunk.model_validate_json(row["chunk_json"]) for row in rows)


class RemoteModelEndpoint:
    """Target Node execution authority for Hub-attested model invocations."""

    def __init__(
        self,
        repository: RemoteModelInvocationRepository,
        *,
        core: Any,
        bootstrap: AppConfig,
        paths: RuntimePaths,
        identity: NodeIdentity,
        hub_store: NodeHubStore,
        provider_factory: Callable[[ResolvedModelConfig], ModelProviderPort] = HttpModelProvider,
    ) -> None:
        self.repository = repository
        self._core = core
        self._bootstrap = bootstrap
        self._paths = paths
        self._identity = identity
        self._hub_store = hub_store
        self._provider_factory = provider_factory
        self._lock = asyncio.Lock()
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._cancellations: dict[str, asyncio.Event] = {}
        self._semaphores: dict[str, asyncio.Semaphore] = {}
        self._active_counts: dict[str, int] = {}

    async def invoke(
        self,
        invocation_id: str,
        ticket: str,
        request: ProviderCallRequest,
    ) -> tuple[ProviderChunk, ...]:
        enrollment, claims = self._claims(ticket, invocation_id)
        del enrollment
        managed = await self._managed()
        deployment = managed.model_deployments.get(claims.target_deployment_id)
        if deployment is None or not deployment.enabled or not deployment.share_enabled:
            raise PermissionError("Remote model deployment is not shared")
        digest = materialized_deployment_digest(managed, claims.target_deployment_id)
        if digest != claims.target_materialized_digest:
            raise PermissionError("Remote model deployment material changed")
        request_digest = hashlib.sha256(
            canonical_json(request.model_dump(mode="json"))
        ).hexdigest()
        record, created = self.repository.admit(
            invocation_id,
            request_digest=request_digest,
            deployment_id=claims.target_deployment_id,
            materialized_digest=digest,
        )
        async with self._lock:
            task = self._tasks.get(invocation_id)
            if created:
                cancellation = asyncio.Event()
                task = asyncio.create_task(
                    self._execute(
                        invocation_id,
                        managed,
                        claims.target_deployment_id,
                        request,
                        cancellation,
                    ),
                    name=f"remote-model-{invocation_id}",
                )
                self._tasks[invocation_id] = task
                self._cancellations[invocation_id] = cancellation
            elif record["state"] == "running" and task is None:
                return self.repository.chunks(invocation_id)
        if task is not None:
            await asyncio.shield(task)
        return self.repository.chunks(invocation_id)

    async def cancel(self, invocation_id: str, ticket: str) -> bool:
        self._claims(ticket, invocation_id)
        async with self._lock:
            cancellation = self._cancellations.get(invocation_id)
        if cancellation is None:
            return False
        cancellation.set()
        return self.repository.cancel(invocation_id)

    async def observations(self) -> tuple[dict[str, Any], ...]:
        managed = await self._managed()
        items = []
        for deployment_id, deployment in managed.model_deployments.items():
            if not deployment.enabled or not deployment.share_enabled:
                continue
            model = managed.models[deployment.model_alias]
            provider = managed.providers[model.provider]
            if provider.driver == "workspace_remote":
                continue
            items.append(
                {
                    "deployment_id": deployment_id,
                    "applied_digest": materialized_deployment_digest(
                        managed, deployment_id
                    ),
                    "health": "healthy",
                    "capabilities": {
                        "streaming": True,
                        "tools": True,
                        "vision": bool(model.supports_vision),
                    },
                    "available_capacity": max(
                        0,
                        deployment.max_remote_concurrency
                        - self._active_counts.get(deployment_id, 0),
                    ),
                }
            )
        return tuple(items)

    async def _execute(
        self,
        invocation_id: str,
        managed: ManagedConfig,
        deployment_id: str,
        request: ProviderCallRequest,
        cancellation: asyncio.Event,
    ) -> None:
        deployment = managed.model_deployments[deployment_id]
        semaphore = self._semaphores.setdefault(
            deployment_id,
            asyncio.Semaphore(deployment.max_remote_concurrency),
        )
        await semaphore.acquire()
        self._active_counts[deployment_id] = (
            self._active_counts.get(deployment_id, 0) + 1
        )
        try:
            if cancellation.is_set():
                self.repository.append(
                    invocation_id,
                    ProviderChunk(
                        finish_reason="error",
                        terminal=True,
                        error_code="cancelled",
                    ),
                )
                return
            model = _resolve_local_model(
                managed,
                deployment.model_alias,
                self._bootstrap,
                self._paths,
            )
            provider = self._provider_factory(model)
            emitted_terminal = False
            async for chunk in provider.stream(request, cancellation):
                self.repository.append(invocation_id, chunk)
                emitted_terminal = emitted_terminal or chunk.terminal
            if not emitted_terminal:
                self.repository.append(
                    invocation_id,
                    ProviderChunk(
                        finish_reason="error",
                        terminal=True,
                        error_code=(
                            "cancelled" if cancellation.is_set() else "provider_failed"
                        ),
                        provider_model=model.alias,
                    ),
                )
        except asyncio.CancelledError:
            self.repository.append(
                invocation_id,
                ProviderChunk(
                    finish_reason="error",
                    terminal=True,
                    error_code="outcome_unknown",
                ),
            )
            raise
        except Exception:  # noqa: BLE001
            self.repository.append(
                invocation_id,
                ProviderChunk(
                    finish_reason="error",
                    terminal=True,
                    error_code="provider_failed",
                ),
            )
        finally:
            self._active_counts[deployment_id] = max(
                0, self._active_counts.get(deployment_id, 1) - 1
            )
            semaphore.release()
            async with self._lock:
                self._tasks.pop(invocation_id, None)
                self._cancellations.pop(invocation_id, None)

    def _claims(self, ticket: str, invocation_id: str):
        enrollment = self._hub_store.load()
        if enrollment is None:
            raise PermissionError("Node is not enrolled in a Workspace HubService")
        claims = verify_resource_ticket(
            ticket,
            enrollment.hub_signing_public_key,
            expected_hub_id=enrollment.hub_id,
            expected_target_node_id=self._identity.node_id,
        )
        if claims.invocation_id != invocation_id:
            raise PermissionError("Resource invocation identity mismatch")
        return enrollment, claims

    async def _managed(self) -> ManagedConfig:
        revision, _state, _generations = await self._core.get_config_current(
            self._bootstrap.owner_principal_id
        )
        return revision.document


class RemoteModelProvider(ModelProviderPort):
    """Caller Node Provider: direct TLS first, then the same invocation via Relay."""

    def __init__(
        self,
        model: ResolvedModelConfig,
        *,
        paths: RuntimePaths,
        client_factory: Callable[..., Any] = httpx.AsyncClient,
        clock=time.time,
    ) -> None:
        if not model.remote_deployment_id:
            raise ValueError("Workspace remote Provider requires a deployment ID")
        self._model = model
        self._paths = paths
        self._client_factory = client_factory
        self._clock = clock

    @property
    def model_alias(self) -> str:
        return self._model.alias

    async def health_check(self) -> HealthStatus:
        try:
            enrollment = self._enrollment()
            async with self._client_factory(timeout=5.0) as client:
                response = await client.get(f"{enrollment.hub_url}/health")
                response.raise_for_status()
            return HealthStatus(healthy=True, detail=self.model_alias)
        except Exception:  # noqa: BLE001
            return HealthStatus(
                healthy=False,
                detail=f"Workspace model unavailable: {self.model_alias}",
            )

    def stream(
        self,
        request: ProviderCallRequest,
        cancellation: asyncio.Event,
    ) -> AsyncIterator[ProviderChunk]:
        return self._stream(request, cancellation)

    async def _stream(
        self,
        request: ProviderCallRequest,
        cancellation: asyncio.Event,
    ) -> AsyncIterator[ProviderChunk]:
        invocation_id = "inv_" + hashlib.sha256(
            f"{self.model_alias}:{request.call_id}".encode()
        ).hexdigest()[:48]
        try:
            ticket = await self._issue_ticket(invocation_id)
            body = {"ticket": ticket, "request": request.model_dump(mode="json")}
            result: dict[str, Any] | None = None
            if self._model.direct_gateway_url:
                try:
                    result = await self._direct(invocation_id, body)
                except (httpx.HTTPError, OSError, ValueError):
                    result = None
            if result is None:
                result = await self._relay(invocation_id, ticket, body)
            for raw in result.get("chunks", ()):
                if cancellation.is_set():
                    return
                yield ProviderChunk.model_validate(raw)
            if not result.get("chunks"):
                raise ValueError("Remote model returned no ProviderChunk")
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            if not cancellation.is_set():
                yield ProviderChunk(
                    finish_reason="error",
                    terminal=True,
                    error_code="remote_provider_failed",
                    provider_model=self.model_alias,
                )

    async def _issue_ticket(self, invocation_id: str) -> str:
        enrollment = self._enrollment()
        identity = self._identity()
        timestamp = float(self._clock())
        nonce = secrets.token_urlsafe(24)
        transcript = {
            "audience": "knoa-resource-ticket-request-v1",
            "workspace_id": enrollment.hub_id,
            "invocation_id": invocation_id,
            "caller_node_id": identity.node_id,
            "target_deployment_id": self._model.remote_deployment_id,
            "max_deadline": self._model.timeout,
            "timestamp": timestamp,
            "nonce": nonce,
        }
        payload = {
            **transcript,
            "signature": identity.sign(canonical_json(transcript)),
        }
        async with self._client_factory(timeout=15.0) as client:
            response = await client.post(
                f"{enrollment.hub_url}/v1/resource-invocation-tickets",
                json=payload,
            )
        response.raise_for_status()
        ticket = str(response.json()["ticket"])
        claims = verify_resource_ticket(
            ticket,
            enrollment.hub_signing_public_key,
            expected_hub_id=enrollment.hub_id,
        )
        if claims.invocation_id != invocation_id:
            raise PermissionError("Hub returned a mismatched invocation ticket")
        return ticket

    async def _direct(self, invocation_id: str, body: dict[str, Any]) -> dict[str, Any]:
        base = self._model.direct_gateway_url.rstrip("/")
        parsed = urlsplit(base)
        if parsed.scheme != "https" and parsed.hostname not in {"127.0.0.1", "::1", "localhost"}:
            raise ValueError("Direct resource transport requires HTTPS")
        async with self._client_factory(timeout=self._model.timeout) as client:
            response = await client.post(
                f"{base}/v1/resource-invocations/{quote(invocation_id, safe='')}",
                json=body,
            )
        response.raise_for_status()
        return response.json()

    async def _relay(
        self,
        invocation_id: str,
        ticket: str,
        body: dict[str, Any],
    ) -> dict[str, Any]:
        enrollment = self._enrollment()
        pending = create_resource_client_hello(
            self._identity(),
            ticket,
            enrollment.hub_signing_public_key,
            expected_hub_id=enrollment.hub_id,
            clock=self._clock,
        )
        websocket_url = _websocket_url(
            enrollment.hub_url, "/v1/relay/resource-client"
        )
        async with websockets.connect(
            websocket_url,
            open_timeout=15,
            ping_interval=20,
            ping_timeout=20,
            max_size=2 * 1024 * 1024,
        ) as websocket:
            await websocket.send(json.dumps({"ticket": ticket}))
            ready = json.loads(await websocket.recv())
            session_id = str(ready.get("session_id", ""))
            if ready.get("ready") is not True or session_id != pending.claims.ticket_id:
                raise PermissionError("Resource Relay rejected ticket")
            await _send_relay_plaintext(
                websocket,
                session_id=session_id,
                stream_id=0,
                sequence=0,
                payload=pending.hello.model_dump_json().encode(),
            )
            first = RelayFrame.model_validate(json.loads(await websocket.recv()).get("frame"))
            first.validate_bounds()
            server_hello = ResourceServerHello.model_validate_json(
                decode_base64url(first.ciphertext)
            )
            cipher = finish_resource_client_handshake(
                pending, server_hello, session_id=session_id
            )
            raw_body = canonical_json(body)
            await _send_relay_encrypted(
                websocket,
                session_id,
                1,
                cipher,
                {
                    "type": "request_start",
                    "method": "POST",
                    "path": f"/v1/resource-invocations/{quote(invocation_id, safe='')}",
                    "headers": {"content-type": "application/json"},
                    "body_length": len(raw_body),
                },
            )
            for offset in range(0, len(raw_body), _RELAY_BODY_CHUNK):
                await _send_relay_encrypted(
                    websocket,
                    session_id,
                    1,
                    cipher,
                    {
                        "type": "request_body",
                        "data": encode_base64url(
                            raw_body[offset : offset + _RELAY_BODY_CHUNK]
                        ),
                    },
                )
            await _send_relay_encrypted(
                websocket,
                session_id,
                1,
                cipher,
                {"type": "request_end"},
            )
            response_body = bytearray()
            status = 0
            async for raw in websocket:
                frame = RelayFrame.model_validate(json.loads(raw).get("frame"))
                frame.validate_bounds()
                if frame.session_id != session_id or frame.stream_id != 1:
                    continue
                message = cipher.decrypt(
                    frame.sequence, decode_base64url(frame.ciphertext)
                )
                kind = message.get("type")
                if kind == "response_start":
                    status = int(message.get("status", 0))
                elif kind == "response_body":
                    response_body.extend(
                        decode_base64url(str(message.get("data", "")))
                    )
                    if len(response_body) > _MAX_RESPONSE_BYTES:
                        raise ValueError("Remote model response is too large")
                elif kind == "response_end":
                    if not 200 <= status < 300:
                        raise PermissionError("Remote model Node rejected invocation")
                    value = json.loads(response_body)
                    if not isinstance(value, dict):
                        raise ValueError("Remote model response must be an object")
                    return value
                elif kind == "reset":
                    raise ConnectionError("Remote model Relay stream reset")
        raise ConnectionError("Remote model Relay closed before response")

    def _identity(self) -> NodeIdentity:
        return NodeIdentityStore(
            self._paths.data / "node-identity.json"
        ).load_or_create()

    def _enrollment(self) -> NodeHubEnrollment:
        from knoa_platform.node_hub import NodeHubStore

        enrollment = NodeHubStore(self._paths.data / "node-hub.json").load()
        if enrollment is None:
            raise PermissionError("Node is not enrolled in a Workspace HubService")
        return enrollment


def _resolve_local_model(
    managed: ManagedConfig,
    alias: str,
    bootstrap: AppConfig,
    paths: RuntimePaths,
) -> ResolvedModelConfig:
    model = managed.models[alias]
    provider = managed.providers[model.provider]
    if provider.driver == "workspace_remote":
        raise ValueError("A shared ModelDeployment cannot target a remote Provider")
    api_key = ""
    if provider.api_key_env:
        import os

        api_key = os.environ.get(provider.api_key_env, "")
    elif provider.api_key_ref:
        api_key = SecretStore(paths.secrets / "providers").get(provider.api_key_ref)
    elif model.provider in bootstrap.providers:
        api_key = bootstrap.providers[model.provider].api_key.get_secret_value()
    required = (
        provider.requires_api_key
        if provider.requires_api_key is not None
        else provider.driver in {"openai", "openai_compatible", "anthropic"}
    )
    if required and not api_key:
        raise ValueError("Local ModelDeployment Provider secret is not configured")
    return ResolvedModelConfig(
        alias=alias,
        provider_name=model.provider,
        driver=provider.driver,
        server_url=provider.server_url or provider.api_base,
        api_base=provider.api_base,
        api_key=api_key,
        model=model.model,
        supports_vision=model.supports_vision,
        context_window=model.context_window,
        timeout=provider.timeout_seconds,
        thinking=(
            None if model.thinking is None else ThinkingConfig(type=model.thinking)
        ),
    )


async def _send_relay_plaintext(
    websocket: Any,
    *,
    session_id: str,
    stream_id: int,
    sequence: int,
    payload: bytes,
) -> None:
    frame = RelayFrame(
        session_id=session_id,
        stream_id=stream_id,
        frame_type="data",
        sequence=sequence,
        ciphertext_length=len(payload),
        ciphertext=encode_base64url(payload),
    )
    await websocket.send(json.dumps({"frame": frame.model_dump(mode="json")}))


async def _send_relay_encrypted(
    websocket: Any,
    session_id: str,
    stream_id: int,
    cipher: Any,
    message: dict[str, Any],
) -> None:
    sequence, ciphertext = cipher.encrypt(message)
    await _send_relay_plaintext(
        websocket,
        session_id=session_id,
        stream_id=stream_id,
        sequence=sequence,
        payload=ciphertext,
    )


def _websocket_url(base_url: str, path: str) -> str:
    parsed = urlsplit(base_url)
    scheme = "wss" if parsed.scheme == "https" else "ws"
    return urlunsplit(
        (scheme, parsed.netloc, f"{parsed.path.rstrip('/')}{path}", "", "")
    )


__all__ = [
    "RemoteModelEndpoint",
    "RemoteModelInvocationRepository",
    "RemoteModelProvider",
    "materialized_deployment_digest",
]
