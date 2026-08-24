"""Single-Hub enrollment state and the Node's outbound opaque Relay connector."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import platform
import secrets
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx
import websockets
from starlette.requests import Request
from starlette.responses import JSONResponse

from knoa_platform import __version__
from knoa_platform.gateway.identity import GatewayIdentityRepository
from knoa_platform.gateway.protocol import NodeHubEnrollmentRequest
from knoa_platform.hub.relay import RelayFrame
from knoa_platform.node_identity import NodeIdentity
from knoa_platform.private_files import (
    fsync_directory,
    prepare_private_directory,
    restrict_private_file,
    validate_private_file,
)
from knoa_platform.relay_protocol import (
    ClientHello,
    PairingClientHello,
    accept_client_hello,
    accept_pairing_client_hello,
    canonical_json,
    decode_base64url,
    encode_base64url,
)
from knoa_platform.resource_protocol import (
    ResourceClientHello,
    accept_resource_client_hello,
)
from knoa_platform.work_status import product_task_work_status

logger = logging.getLogger(__name__)
_MAX_TUNNEL_BODY_BYTES = 64 * 1024 * 1024
_RESPONSE_CHUNK_BYTES = 192 * 1024
_FORWARDED_REQUEST_HEADERS = {
    "accept",
    "authorization",
    "content-type",
    "if-none-match",
    "last-event-id",
    "range",
    "x-knoa-transport",
}
_FORWARDED_RESPONSE_HEADERS = {
    "accept-ranges",
    "cache-control",
    "content-disposition",
    "content-length",
    "content-range",
    "content-type",
    "etag",
    "x-content-type-options",
    "x-knoa-sha256",
}


@dataclass(frozen=True)
class NodeHubEnrollment:
    hub_url: str
    hub_id: str
    hub_signing_public_key: str
    enrolled_at: float
    display_name: str = ""

    @property
    def workspace_id(self) -> str:
        """Return the Workspace authority scoped by the canonical Hub URL."""

        segments = tuple(
            segment for segment in urlsplit(self.hub_url).path.split("/") if segment
        )
        if len(segments) >= 2 and segments[-2] == "workspaces":
            return _identifier(segments[-1], "Workspace ID")
        return self.hub_id


class NodeHubStore:
    def __init__(self, path: str | Path, *, clock=time.time) -> None:
        self._path = Path(path).expanduser().resolve()
        self._clock = clock

    def load(self) -> NodeHubEnrollment | None:
        if not self._path.exists():
            return None
        try:
            validate_private_file(self._path, label="Node Hub enrollment")
        except RuntimeError as exc:
            raise PermissionError(str(exc)) from exc
        raw = json.loads(self._path.read_text(encoding="utf-8"))
        return NodeHubEnrollment(
            hub_url=_hub_url(str(raw["hub_url"])),
            hub_id=_identifier(str(raw["hub_id"]), "Hub ID"),
            hub_signing_public_key=_public_key(str(raw["hub_signing_public_key"])),
            enrolled_at=float(raw["enrolled_at"]),
            display_name=str(raw.get("display_name") or "").strip(),
        )

    def save(
        self,
        *,
        hub_url: str,
        hub_id: str,
        hub_signing_public_key: str,
        display_name: str = "",
    ) -> NodeHubEnrollment:
        enrollment = NodeHubEnrollment(
            hub_url=_hub_url(hub_url),
            hub_id=_identifier(hub_id, "Hub ID"),
            hub_signing_public_key=_public_key(hub_signing_public_key),
            enrolled_at=float(self._clock()),
            display_name=_display_name(display_name, allow_empty=True),
        )
        self._write(enrollment)
        return enrollment

    def update_display_name(self, display_name: str) -> NodeHubEnrollment:
        current = self.load()
        if current is None:
            raise LookupError("Node is not enrolled")
        enrollment = NodeHubEnrollment(
            hub_url=current.hub_url,
            hub_id=current.hub_id,
            hub_signing_public_key=current.hub_signing_public_key,
            enrolled_at=current.enrolled_at,
            display_name=_display_name(display_name),
        )
        self._write(enrollment)
        return enrollment

    def _write(self, enrollment: NodeHubEnrollment) -> None:
        prepare_private_directory(
            self._path.parent, label="Node Hub enrollment directory"
        )
        temporary = self._path.with_name(
            f".{self._path.name}.{secrets.token_hex(8)}.tmp"
        )
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump(
                    asdict(enrollment), stream, sort_keys=True, separators=(",", ":")
                )
                stream.flush()
                os.fsync(stream.fileno())
            restrict_private_file(temporary)
            os.replace(temporary, self._path)
            fsync_directory(self._path.parent)
        finally:
            temporary.unlink(missing_ok=True)

    def clear(self) -> None:
        self._path.unlink(missing_ok=True)


class NodeHubService:
    def __init__(
        self,
        store: NodeHubStore,
        identity: NodeIdentity,
        *,
        clock=time.time,
    ) -> None:
        self.store = store
        self.identity = identity
        self._clock = clock

    async def enroll(self, request: NodeHubEnrollmentRequest) -> NodeHubEnrollment:
        hub_url = _hub_url(request.hub_url)
        transcript = {
            "audience": "knoa-node-enrollment-v1",
            "hub_id": request.hub_id,
            "grant_id": request.grant_id,
            "challenge": request.challenge,
            "node_id": self.identity.node_id,
            "signing_public_key": self.identity.signing_public_key,
            "signing_key_version": self.identity.signing_key_version,
            "configuration_public_key": self.identity.configuration_public_key,
            "configuration_key_version": self.identity.configuration_key_version,
        }
        payload = {
            **transcript,
            "grant_secret": request.grant_secret,
            "display_name": request.display_name,
            "platform": platform.system().lower(),
            "version": __version__,
            "signature": self.identity.sign(canonical_json(transcript)),
        }
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(f"{hub_url}/v1/nodes/enroll", json=payload)
        if response.status_code != 201:
            raise PermissionError("Hub rejected Node enrollment")
        try:
            body = response.json()
            node = body["node"]
            hub = body["hub"]
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("Hub returned an invalid enrollment response") from exc
        if (
            not isinstance(node, dict)
            or not isinstance(hub, dict)
            or node.get("node_id") != self.identity.node_id
            or node.get("signing_public_key") != self.identity.signing_public_key
            or hub.get("hub_id") != request.hub_id
            or hub.get("signing_public_key") != request.hub_signing_public_key
        ):
            raise PermissionError("Hub enrollment identity mismatch")
        return self.store.save(
            hub_url=hub_url,
            hub_id=request.hub_id,
            hub_signing_public_key=request.hub_signing_public_key,
            display_name=request.display_name,
        )

    def update_display_name(self, display_name: str) -> NodeHubEnrollment:
        return self.store.update_display_name(display_name)

    async def control_state(self) -> dict[str, Any]:
        enrollment = self.store.load()
        if enrollment is None:
            raise PermissionError("Node is not enrolled in a Workspace Hub")
        payload = self._signed_control_payload(
            enrollment,
            "knoa-node-control-state-v1",
            {},
        )
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(
                f"{enrollment.hub_url}/v1/node-control/state", json=payload
            )
        response.raise_for_status()
        value = response.json()
        if not isinstance(value, dict) or value.get("node_id") != self.identity.node_id:
            raise ValueError("Hub returned invalid Node control state")
        return value

    async def publish_model_share(self, publication: dict[str, Any]) -> dict[str, Any]:
        enrollment = self.store.load()
        if enrollment is None:
            raise PermissionError("Node is not enrolled in a Workspace Hub")
        payload = self._signed_control_payload(
            enrollment,
            "knoa-node-model-share-v1",
            publication,
        )
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.post(
                f"{enrollment.hub_url}/v1/node-control/model-shares", json=payload
            )
        response.raise_for_status()
        value = response.json()
        if not isinstance(value, dict):
            raise ValueError("Hub returned invalid Model share state")
        return value

    async def provision_webhook_route(
        self,
        *,
        principal_id: str,
        task_id: str,
        trigger_id: str,
        display_name: str,
    ) -> dict[str, Any]:
        enrollment = self.store.load()
        if enrollment is None:
            return {}
        payload = self._signed_control_payload(
            enrollment,
            "knoa-webhook-route-provision-v1",
            {
                "principal_id": principal_id,
                "task_id": task_id,
                "trigger_id": trigger_id,
                "display_name": display_name,
            },
        )
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(f"{enrollment.hub_url}/v1/webhook-routes", json=payload)
        response.raise_for_status()
        result = response.json()
        origin = urlsplit(enrollment.hub_url)
        result["public_url"] = urlunsplit((origin.scheme, origin.netloc, f"/hooks/v1/{result['route_id']}", "", ""))
        result["signing_example"] = {
            "headers": ["X-Knoa-Event-Id", "X-Knoa-Timestamp", "X-Knoa-Signature"],
            "transcript": "<event-id>\\n<unix-timestamp>\\n<raw-body>",
            "algorithm": "HMAC-SHA256",
        }
        return result

    async def rotate_webhook_secret(self, route_id: str) -> dict[str, Any]:
        enrollment = self.store.load()
        if enrollment is None:
            raise PermissionError("Node is not enrolled in a Workspace Hub")
        payload = self._signed_control_payload(
            enrollment, "knoa-webhook-secret-rotate-v1", {"route_id": route_id}
        )
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(
                f"{enrollment.hub_url}/v1/webhook-routes/{route_id}/rotate-secret", json=payload
            )
        response.raise_for_status()
        return response.json()

    async def delete_webhook_route(self, route_id: str) -> None:
        enrollment = self.store.load()
        if enrollment is None:
            return
        payload = self._signed_control_payload(
            enrollment, "knoa-webhook-route-delete-v1", {"route_id": route_id}
        )
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.request(
                "DELETE", f"{enrollment.hub_url}/v1/webhook-routes/{route_id}", json=payload
            )
        response.raise_for_status()

    def _signed_control_payload(
        self,
        enrollment: NodeHubEnrollment,
        audience: str,
        values: dict[str, Any],
    ) -> dict[str, Any]:
        timestamp = float(self._clock())
        payload = {
            "node_id": self.identity.node_id,
            **values,
            "timestamp": timestamp,
            "nonce": secrets.token_urlsafe(24),
        }
        transcript = {
            "audience": audience,
            "workspace_id": enrollment.workspace_id,
            **payload,
        }
        return {
            "audience": audience,
            **payload,
            "signature": self.identity.sign(canonical_json(transcript)),
        }


class NodeHubRoutes:
    async def _hub_status(self, request: Request) -> JSONResponse:
        authenticated = self._authorize(request, limit=60)
        if isinstance(authenticated, JSONResponse):
            return authenticated
        return JSONResponse(self._node_relay.status)

    async def _hub_enroll(self, request: Request) -> JSONResponse:
        authenticated = self._authorize(request, limit=10)
        if isinstance(authenticated, JSONResponse):
            return authenticated
        parsed = await self._parse_body(
            request,
            NodeHubEnrollmentRequest,
            max_body_bytes=16 * 1024,
        )
        if isinstance(parsed, JSONResponse):
            return parsed
        try:
            enrollment = await self._node_hub.enroll(parsed)
            await self._node_relay.restart()
        except PermissionError:
            return JSONResponse({"error": "rejected"}, status_code=401)
        except (httpx.HTTPError, ValueError):
            return JSONResponse({"error": "unavailable"}, status_code=503)
        return JSONResponse(
            {"enrollment": asdict(enrollment), "relay_connected": False},
            status_code=201,
        )

    async def _hub_remove(self, request: Request) -> JSONResponse:
        authenticated = self._authorize(request, limit=10)
        if isinstance(authenticated, JSONResponse):
            return authenticated
        await self._node_relay.stop()
        self._node_hub.store.clear()
        return JSONResponse({"removed": True})


@dataclass
class _RequestStream:
    method: str
    path: str
    headers: dict[str, str]
    expected_length: int
    body: bytearray


@dataclass
class _RelaySession:
    cipher: Any
    streams: dict[int, _RequestStream]
    send_lock: asyncio.Lock
    kind: str = "app"


class NodeRelayManager:
    def __init__(
        self,
        *,
        store: NodeHubStore,
        identity: NodeIdentity,
        identities: GatewayIdentityRepository,
        app: Any,
        core: Any | None = None,
        remote_models: Any | None = None,
        direct_gateway_url: str = "",
        owner_principal_id: str = "",
        clock=time.time,
    ) -> None:
        self._store = store
        self._identity = identity
        self._identities = identities
        self._app = app
        self._core = core
        self._remote_models = remote_models
        self._direct_gateway_url = direct_gateway_url.strip().rstrip("/")
        self._owner_principal_id = owner_principal_id
        self._control = NodeHubService(store, identity, clock=clock)
        self._clock = clock
        self._task: asyncio.Task[None] | None = None
        self._generation = 0
        self._connected = False
        self._last_error = ""

    @property
    def status(self) -> dict[str, Any]:
        enrollment = self._store.load()
        return {
            "enrolled": enrollment is not None,
            "hub": None if enrollment is None else asdict(enrollment),
            "relay_connected": self._connected,
            "last_error": self._last_error,
        }

    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        if self._store.load() is None:
            return
        self._generation += 1
        generation = self._generation
        self._task = asyncio.create_task(
            self._run(generation), name="knoa-node-relay-connector"
        )

    async def restart(self) -> None:
        await self.stop()
        await self.start()

    async def stop(self) -> None:
        self._generation += 1
        task, self._task = self._task, None
        self._connected = False
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def _run(self, generation: int) -> None:
        delay = 1.0
        while generation == self._generation:
            enrollment = self._store.load()
            if enrollment is None:
                return
            try:
                await self._connect(enrollment)
                delay = 1.0
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                self._connected = False
                self._last_error = type(exc).__name__
                logger.warning("Node Relay connection lost: %s", exc)
            await asyncio.sleep(delay)
            delay = min(delay * 2, 30.0)

    async def _connect(self, enrollment: NodeHubEnrollment) -> None:
        websocket_url = _websocket_url(enrollment.hub_url, "/v1/relay/node")
        sessions: dict[str, _RelaySession] = {}
        async with websockets.connect(
            websocket_url,
            open_timeout=15,
            ping_interval=20,
            ping_timeout=20,
            max_size=2 * 1024 * 1024,
        ) as websocket:
            await websocket.send(
                json.dumps(
                    _presence(
                        self._identity,
                        enrollment,
                        self._clock,
                        direct_gateway_url=self._direct_gateway_url,
                    )
                )
            )
            ready = json.loads(await websocket.recv())
            if (
                ready.get("ready") is not True
                or ready.get("node_id") != self._identity.node_id
            ):
                raise PermissionError("Relay rejected Node presence")
            remote_display_name = str(ready.get("display_name") or "").strip()
            if not enrollment.display_name and remote_display_name:
                enrollment = self._store.update_display_name(remote_display_name)
            self._connected = True
            self._last_error = ""
            publisher = asyncio.create_task(
                self._publish_observations(enrollment),
                name="knoa-deployment-observations",
            )
            try:
                async for raw in websocket:
                    message = json.loads(raw)
                    frame = RelayFrame.model_validate(message.get("frame"))
                    frame.validate_bounds()
                    await self._receive_frame(websocket, enrollment, sessions, frame)
            finally:
                publisher.cancel()
                await asyncio.gather(publisher, return_exceptions=True)
        self._connected = False

    async def _receive_frame(
        self,
        websocket: Any,
        enrollment: NodeHubEnrollment,
        sessions: dict[str, _RelaySession],
        frame: RelayFrame,
    ) -> None:
        if frame.frame_type != "data":
            if frame.frame_type == "reset":
                sessions.pop(frame.session_id, None)
            return
        raw = decode_base64url(frame.ciphertext)
        session = sessions.get(frame.session_id)
        if session is None:
            envelope = json.loads(raw)
            if envelope.get("type") == "resource_client_hello":
                hello = ResourceClientHello.model_validate(envelope)
                server_hello, cipher = accept_resource_client_hello(
                    hello,
                    session_id=frame.session_id,
                    hub_id=enrollment.hub_id,
                    workspace_id=enrollment.workspace_id,
                    hub_signing_public_key=enrollment.hub_signing_public_key,
                    node_identity=self._identity,
                    clock=self._clock,
                )
                kind = "resource"
            elif envelope.get("type") == "pairing_client_hello":
                hello = PairingClientHello.model_validate(envelope)
                server_hello, cipher = accept_pairing_client_hello(
                    hello,
                    session_id=frame.session_id,
                    hub_id=enrollment.hub_id,
                    hub_signing_public_key=enrollment.hub_signing_public_key,
                    node_identity=self._identity,
                    clock=self._clock,
                )
                kind = "pairing"
            else:
                hello = ClientHello.model_validate(envelope)
                device = self._identities.active_device_by_id(hello.device_id)
                server_hello, cipher = accept_client_hello(
                    hello,
                    session_id=frame.session_id,
                    hub_id=enrollment.hub_id,
                    hub_signing_public_key=enrollment.hub_signing_public_key,
                    node_identity=self._identity,
                    device=device,
                    clock=self._clock,
                )
                kind = "app"
            session = _RelaySession(
                cipher=cipher,
                streams={},
                send_lock=asyncio.Lock(),
                kind=kind,
            )
            sessions[frame.session_id] = session
            await _send_plaintext(
                websocket,
                session_id=frame.session_id,
                stream_id=0,
                sequence=0,
                payload=server_hello.model_dump_json().encode("utf-8"),
            )
            return
        if session.cipher.expires_at <= self._clock():
            sessions.pop(frame.session_id, None)
            raise PermissionError("Relay session expired")
        message = session.cipher.decrypt(frame.sequence, raw)
        kind = message.get("type")
        if kind == "request_start":
            self._request_start(session, frame.stream_id, message)
        elif kind == "request_body":
            self._request_body(session, frame.stream_id, message)
        elif kind == "request_end":
            stream = session.streams.pop(frame.stream_id, None)
            if stream is None or len(stream.body) != stream.expected_length:
                raise ValueError("Relay request body length mismatch")
            asyncio.create_task(
                self._dispatch(
                    websocket, frame.session_id, frame.stream_id, session, stream
                ),
                name=f"knoa-relay-request-{frame.stream_id}",
            )
        elif kind == "reset":
            session.streams.pop(frame.stream_id, None)
        else:
            raise ValueError("Unknown Relay message type")

    @staticmethod
    def _request_start(
        session: _RelaySession, stream_id: int, message: dict[str, Any]
    ) -> None:
        method = str(message.get("method", "")).upper()
        path = str(message.get("path", ""))
        length = int(message.get("body_length", -1))
        raw_headers = message.get("headers", {})
        resource_path = path.startswith("/v1/resource-invocations/") or path == (
            "/v1/resource-p2p/offer"
        )
        pairing_path = (method, path) in {
            ("POST", "/v1/pair/challenge"),
            ("POST", "/v1/pair/complete"),
        }
        if (
            stream_id <= 0
            or stream_id in session.streams
            or method not in {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"}
            or not path.startswith("/")
            or path.startswith("//")
            or "://" in path
            or len(path) > 4096
            or length < 0
            or length > _MAX_TUNNEL_BODY_BYTES
            or not isinstance(raw_headers, dict)
            or (
                session.kind == "resource"
                and (method not in {"POST", "DELETE"} or not resource_path)
            )
            or (session.kind == "pairing" and not pairing_path)
        ):
            raise ValueError("Relay request start rejected")
        headers = {
            str(key).lower(): str(value)
            for key, value in raw_headers.items()
            if str(key).lower() in _FORWARDED_REQUEST_HEADERS
            and len(str(value)) <= 8192
        }
        session.streams[stream_id] = _RequestStream(
            method=method,
            path=path,
            headers=headers,
            expected_length=length,
            body=bytearray(),
        )

    async def _publish_observations(
        self,
        enrollment: NodeHubEnrollment,
    ) -> None:
        while True:
            try:
                await self.sync_workspace_resources()
                async with httpx.AsyncClient(timeout=15.0) as client:
                    if self._remote_models is not None:
                        observations = await self._remote_models.observations()
                        for item in observations:
                            observed_at = float(self._clock())
                            payload = {
                                "node_id": self._identity.node_id,
                                **item,
                                "health_epoch": max(1, int(observed_at)),
                                "observed_at": observed_at,
                                "expires_at": observed_at + 90,
                            }
                            transcript = {
                                "audience": "knoa-deployment-observation-v1",
                                "workspace_id": enrollment.workspace_id,
                                **payload,
                            }
                            payload["signature"] = self._identity.sign(
                                canonical_json(transcript)
                            )
                            response = await client.post(
                                f"{enrollment.hub_url}/v1/deployment-observations",
                                json=payload,
                            )
                            response.raise_for_status()
                    await self._publish_work_projections(client, enrollment)
                    await self._publish_notification_intents(client, enrollment)
                    await self._pull_webhook_events(client, enrollment)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                logger.warning("Deployment observation publish failed: %s", exc)
            await asyncio.sleep(30)

    async def _publish_notification_intents(
        self,
        client: httpx.AsyncClient,
        enrollment: NodeHubEnrollment,
    ) -> None:
        if self._core is None:
            return
        for principal_id in self._identities.principal_ids():
            intents = await self._core.list_notification_intents(
                principal_id,
                after_sequence=0,
                limit=100,
                pending_only=True,
            )
            for intent in intents:
                payload = {
                    "audience": "knoa-notification-intent-v1",
                    "workspace_id": enrollment.workspace_id,
                    "node_id": self._identity.node_id,
                    "nonce": secrets.token_urlsafe(24),
                    "timestamp": float(self._clock()),
                    "intent_id": intent.intent_id,
                    "principal_id": intent.principal_id,
                    "category": intent.category,
                    "work_kind": intent.work_kind,
                    "work_id": intent.work_id,
                    "execution_id": intent.execution_id,
                    "semantic_code": intent.semantic_code,
                    "parameters": intent.parameters,
                    "deep_link": intent.deep_link,
                    "dedupe_key": intent.dedupe_key,
                    "priority": intent.priority,
                    "expires_at": intent.expires_at,
                    "source_sequence": intent.source_sequence,
                    "created_at": intent.created_at,
                }
                payload["signature"] = self._identity.sign(canonical_json(payload))
                response = await client.post(
                    f"{enrollment.hub_url}/v1/notification-intents",
                    json=payload,
                )
                response.raise_for_status()
                await self._core.mark_notification_intent_projected(
                    principal_id,
                    intent.intent_id,
                )

    async def _pull_webhook_events(
        self,
        client: httpx.AsyncClient,
        enrollment: NodeHubEnrollment,
    ) -> None:
        if self._core is None:
            return
        request = self._control._signed_control_payload(
            enrollment, "knoa-webhook-event-pull-v1", {"limit": 50}
        )
        response = await client.post(
            f"{enrollment.hub_url}/v1/webhook-events/pull", json=request
        )
        response.raise_for_status()
        accepted: list[int] = []
        for item in response.json().get("events", ()):
            await self._core.fire_trigger(
                str(item["principal_id"]),
                str(item["trigger_id"]),
                str(item["external_event_id"]),
                item.get("payload", {}),
            )
            accepted.append(int(item["ingress_id"]))
        if not accepted:
            return
        ack = self._control._signed_control_payload(
            enrollment, "knoa-webhook-event-ack-v1", {"ingress_ids": accepted}
        )
        ack_response = await client.post(
            f"{enrollment.hub_url}/v1/webhook-events/ack", json=ack
        )
        ack_response.raise_for_status()

    async def workspace_resource_state(self) -> dict[str, Any]:
        return await self._control.control_state()

    async def sync_workspace_resources(self) -> dict[str, Any]:
        if self._core is None or not self._owner_principal_id:
            return {}
        workspace_state = await self._control.control_state()
        revision, _state, _generations = await self._core.get_config_current(
            self._owner_principal_id
        )
        managed = revision.document
        published: list[dict[str, Any]] = []
        local_deployment_ids = {
            deployment_id
            for deployment_id, deployment in managed.model_deployments.items()
            if managed.providers[managed.models[deployment.model_alias].provider].driver
            != "workspace_remote"
        }
        resources = {
            str(item["resource_id"]): item
            for item in workspace_state.get("resources", ())
            if isinstance(item, dict)
        }
        for deployment in workspace_state.get("deployments", ()):
            if (
                not isinstance(deployment, dict)
                or str(deployment.get("target_node_id", "")) != self._identity.node_id
                or str(deployment.get("deployment_id", "")) in local_deployment_ids
                or not bool(deployment.get("enabled"))
            ):
                continue
            resource = resources.get(str(deployment.get("resource_id", "")))
            if resource is None:
                continue
            spec = resource.get("spec", {})
            capabilities = spec.get("declared_capabilities", {}) if isinstance(spec, dict) else {}
            deployment_spec = deployment.get("spec", {})
            publication = {
                "deployment_id": str(deployment["deployment_id"]),
                "resource_id": str(resource["resource_id"]),
                "display_name": str(resource.get("display_name") or deployment["deployment_id"]),
                "model_identity": str(spec.get("model_identity") or deployment["deployment_id"]),
                "provider_protocol": str(spec.get("provider_protocol") or "openai_compatible"),
                "supports_vision": bool(capabilities.get("vision")) if isinstance(capabilities, dict) else False,
                "materialized_digest": str(deployment_spec.get("materialized_digest") or "0" * 64),
                "max_remote_concurrency": int(deployment_spec.get("max_remote_concurrency") or 1),
                "allowed_node_ids": [],
                "enabled": False,
            }
            published.append(await self._control.publish_model_share(publication))
        for deployment_id, deployment in managed.model_deployments.items():
            model = managed.models[deployment.model_alias]
            provider = managed.providers[model.provider]
            if provider.driver == "workspace_remote":
                continue
            publication = {
                "deployment_id": deployment_id,
                "resource_id": deployment.resource_id,
                "display_name": deployment.display_name or model.model or deployment.model_alias,
                "model_identity": model.model or deployment.model_alias,
                "provider_protocol": (
                    "anthropic" if provider.driver == "anthropic" else "openai_compatible"
                ),
                "supports_vision": bool(model.supports_vision),
                "materialized_digest": hashlib.sha256(
                    canonical_json(
                        {
                            "deployment_id": deployment_id,
                            "deployment": deployment.model_dump(mode="json"),
                            "model": model.model_dump(mode="json"),
                            "provider": provider.model_dump(
                                mode="json", exclude={"api_key_ref", "api_key_env"}
                            ),
                        }
                    )
                ).hexdigest(),
                "max_remote_concurrency": deployment.max_remote_concurrency,
                "allowed_node_ids": list(deployment.allowed_node_ids),
                "enabled": bool(deployment.enabled and deployment.share_enabled),
            }
            published.append(await self._control.publish_model_share(publication))
        return {"published": published}

    async def _publish_work_projections(
        self,
        client: httpx.AsyncClient,
        enrollment: NodeHubEnrollment,
    ) -> None:
        if self._core is None:
            return
        for principal_id in self._identities.principal_ids():
            sessions, _cursor = await self._core.list_conversation_sessions(
                principal_id,
                include_archived=True,
                limit=200,
            )
            tasks = await self._core.list_product_tasks(
                principal_id,
                include_archived=True,
                limit=200,
            )
            projections = [
                {
                    "entity_kind": "conversation",
                    "entity_id": session.session_handle,
                    "principal_id": principal_id,
                    "title": session.title,
                    "state": session.state.value,
                    "progress": None,
                    "summary": "",
                    "approval_summary": "",
                    "artifact_refs": [],
                    "source_generation": session.revision,
                    "projection_seq": session.revision,
                    "source_created_at": session.created_at,
                    "source_updated_at": session.updated_at,
                    "payload": {
                        "agent_id": session.agent_id,
                        "turn_count": session.turn_count,
                        "last_turn_at": session.last_turn_at,
                    },
                }
                for session in sessions
                if session.state.value != "archived"
            ]
            projections.extend(
                {
                    "entity_kind": "task",
                    "entity_id": task.task_id,
                    "principal_id": principal_id,
                    "title": task.title,
                    "state": (
                        task.latest_execution_state.value
                        if task.latest_execution_state is not None
                        else task.state.value
                    ),
                    "progress": None,
                    "summary": task.latest_execution_summary,
                    "approval_summary": (
                        f"{task.pending_approval_count} approval(s) pending"
                        if task.pending_approval_count
                        else ""
                    ),
                    "artifact_refs": [],
                    "source_generation": task.revision,
                    "projection_seq": max(
                        1,
                        int(
                            (task.latest_execution_updated_at or task.updated_at)
                            * 1_000_000
                        ),
                    ),
                    "source_created_at": task.created_at,
                    "source_updated_at": task.latest_execution_updated_at
                    or task.updated_at,
                    "payload": {
                        "agent_id": task.agent_id,
                        "launch_kind": task.launch_policy.kind.value,
                        "latest_execution_id": task.latest_execution_id,
                        "execution_count": task.execution_count,
                        "latest_execution_phase": task.latest_execution_phase,
                        "latest_execution_failure_code": task.latest_execution_failure_code,
                        "work_status": product_task_work_status(
                            task.state.value,
                            task.latest_execution_state.value if task.latest_execution_state is not None else None,
                            pending_approval_count=task.pending_approval_count,
                        ).model_dump(mode="json"),
                    },
                }
                for task in tasks
                if task.state.value != "archived"
            )
            for projection in projections:
                material = canonical_json(projection)
                observed_at = float(self._clock())
                payload = {
                    "node_id": self._identity.node_id,
                    **projection,
                    "source_digest": hashlib.sha256(material).hexdigest(),
                    "observed_at": observed_at,
                }
                transcript = {
                    "audience": "knoa-work-projection-v1",
                    "workspace_id": enrollment.workspace_id,
                    **payload,
                }
                payload["signature"] = self._identity.sign(canonical_json(transcript))
                response = await client.post(
                    f"{enrollment.hub_url}/v1/work-projections",
                    json=payload,
                )
                response.raise_for_status()
            # Core's list is authoritative. A deleted conversation/task no
            # longer has a projection to upsert, so explicitly reconcile each
            # principal/kind to remove stale Hub rows.
            for entity_kind in ("conversation", "task"):
                active_ids = [
                    str(item["entity_id"])
                    for item in projections
                    if item["entity_kind"] == entity_kind
                ]
                reconcile = {
                    "node_id": self._identity.node_id,
                    "entity_kind": entity_kind,
                    "principal_id": principal_id,
                    "active_entity_ids": active_ids,
                    "observed_at": float(self._clock()),
                }
                transcript = {
                    "audience": "knoa-work-projection-reconcile-v1",
                    "workspace_id": enrollment.workspace_id,
                    **reconcile,
                }
                reconcile["signature"] = self._identity.sign(canonical_json(transcript))
                response = await client.post(
                    f"{enrollment.hub_url}/v1/work-projections/reconcile",
                    json=reconcile,
                )
                response.raise_for_status()

    @staticmethod
    def _request_body(
        session: _RelaySession, stream_id: int, message: dict[str, Any]
    ) -> None:
        stream = session.streams.get(stream_id)
        if stream is None:
            raise ValueError("Relay request stream is absent")
        chunk = decode_base64url(str(message.get("data", "")))
        stream.body.extend(chunk)
        if (
            len(stream.body) > stream.expected_length
            or len(stream.body) > _MAX_TUNNEL_BODY_BYTES
        ):
            raise ValueError("Relay request body is too large")

    async def _dispatch(
        self,
        websocket: Any,
        session_id: str,
        stream_id: int,
        session: _RelaySession,
        stream: _RequestStream,
    ) -> None:
        try:
            transport = httpx.ASGITransport(app=self._app, client=("relay", 0))
            async with httpx.AsyncClient(
                transport=transport,
                base_url="http://knoa-node.local",
                timeout=120.0,
            ) as client:
                response = await client.request(
                    stream.method,
                    stream.path,
                    headers=stream.headers,
                    content=bytes(stream.body),
                )
            await self._send_encrypted(
                websocket,
                session_id,
                stream_id,
                session,
                {
                    "type": "response_start",
                    "status": response.status_code,
                    "headers": {
                        key.lower(): value
                        for key, value in response.headers.items()
                        if key.lower() in _FORWARDED_RESPONSE_HEADERS
                    },
                    "body_length": len(response.content),
                },
            )
            for offset in range(0, len(response.content), _RESPONSE_CHUNK_BYTES):
                await self._send_encrypted(
                    websocket,
                    session_id,
                    stream_id,
                    session,
                    {
                        "type": "response_body",
                        "data": encode_base64url(
                            response.content[offset : offset + _RESPONSE_CHUNK_BYTES]
                        ),
                    },
                )
            await self._send_encrypted(
                websocket,
                session_id,
                stream_id,
                session,
                {"type": "response_end"},
            )
        except Exception:
            logger.warning("Relay request dispatch failed", exc_info=True)
            try:
                await self._send_encrypted(
                    websocket,
                    session_id,
                    stream_id,
                    session,
                    {"type": "reset", "code": "unavailable"},
                )
            except Exception:  # noqa: BLE001, S110
                pass

    @staticmethod
    async def _send_encrypted(
        websocket: Any,
        session_id: str,
        stream_id: int,
        session: _RelaySession,
        message: dict[str, Any],
    ) -> None:
        async with session.send_lock:
            sequence, ciphertext = session.cipher.encrypt(message)
            await _send_plaintext(
                websocket,
                session_id=session_id,
                stream_id=stream_id,
                sequence=sequence,
                payload=ciphertext,
            )


async def _send_plaintext(
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


def _presence(
    identity: NodeIdentity,
    enrollment: NodeHubEnrollment,
    clock: Any,
    *,
    direct_gateway_url: str = "",
) -> dict[str, Any]:
    timestamp = float(clock())
    nonce = secrets.token_urlsafe(24)
    transcript = {
        "audience": "knoa-node-presence-v1",
        "hub_id": enrollment.hub_id,
        "node_id": identity.node_id,
        "timestamp": timestamp,
        "nonce": nonce,
        "version": __version__,
        "direct_gateway_url": direct_gateway_url,
    }
    if enrollment.display_name:
        transcript["display_name"] = enrollment.display_name
    return {
        **{key: value for key, value in transcript.items() if key not in {"audience", "hub_id"}},
        "signature": identity.sign(canonical_json(transcript)),
    }


def _display_name(value: str, *, allow_empty: bool = False) -> str:
    normalized = str(value).strip()
    if not normalized and allow_empty:
        return ""
    if not normalized or len(normalized) > 80 or any(ord(char) < 32 for char in normalized):
        raise ValueError("Node display name must contain 1-80 printable characters")
    return normalized


def _hub_url(value: str) -> str:
    normalized = value.strip().rstrip("/")
    parsed = urlsplit(normalized)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or parsed.username
        or parsed.password
    ):
        raise ValueError("Hub URL must be an absolute HTTP(S) URL")
    if parsed.query or parsed.fragment:
        raise ValueError("Hub URL must not contain query or fragment")
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", ""))


def _websocket_url(base_url: str, path: str) -> str:
    parsed = urlsplit(base_url)
    scheme = "wss" if parsed.scheme == "https" else "ws"
    return urlunsplit(
        (scheme, parsed.netloc, f"{parsed.path.rstrip('/')}{path}", "", "")
    )


def _identifier(value: str, label: str) -> str:
    normalized = value.strip()
    if (
        not normalized
        or len(normalized) > 128
        or any(ord(char) < 33 for char in normalized)
    ):
        raise ValueError(f"{label} is invalid")
    return normalized


def _public_key(value: str) -> str:
    normalized = value.strip()
    try:
        if len(decode_base64url(normalized)) != 32:
            raise ValueError
    except ValueError as exc:
        raise ValueError("Hub signing public key is invalid") from exc
    return normalized


__all__ = [
    "NodeHubEnrollment",
    "NodeHubEnrollmentRequest",
    "NodeHubRoutes",
    "NodeHubService",
    "NodeHubStore",
    "NodeRelayManager",
]
