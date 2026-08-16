from __future__ import annotations

import asyncio
import base64
import json
import os
from pathlib import Path

os.environ.setdefault("CRYPTOGRAPHY_OPENSSL_NO_LEGACY", "1")

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.asymmetric.x25519 import (
    X25519PrivateKey,
    X25519PublicKey,
)
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route

from knoa_platform.gateway.identity import GatewayIdentityRepository
from knoa_platform.hub.relay import RelayFrame
from knoa_platform.hub.repository import HubRepository
from knoa_platform.hub.service import HubService
from knoa_platform.node_hub import NodeHubEnrollment, NodeHubStore, NodeRelayManager
from knoa_platform.node_identity import NodeIdentityStore
from knoa_platform.relay_protocol import (
    ClientHello,
    canonical_json,
    decode_base64url,
    derive_session_keys,
    encode_base64url,
)


def _public_key(key: Ed25519PrivateKey) -> str:
    return encode_base64url(
        key.public_key().public_bytes(
            serialization.Encoding.Raw,
            serialization.PublicFormat.Raw,
        )
    )


def _frame(session_id: str, stream_id: int, sequence: int, payload: bytes) -> RelayFrame:
    return RelayFrame(
        session_id=session_id,
        stream_id=stream_id,
        frame_type="data",
        sequence=sequence,
        ciphertext_length=len(payload),
        ciphertext=encode_base64url(payload),
    )


def _aad(session_id: str, direction: str, sequence: int) -> bytes:
    return canonical_json({
        "audience": "knoa-node-packet-v1",
        "session_id": session_id,
        "direction": direction,
        "sequence": sequence,
    })


async def _echo(request: Request) -> Response:
    return Response(
        await request.body(),
        status_code=201,
        media_type=request.headers.get("content-type", "application/octet-stream"),
        headers={"X-Knoa-SHA256": "digest-a"},
    )


class _WebSocket:
    def __init__(self) -> None:
        self.messages: list[dict] = []
        self.changed = asyncio.Event()

    async def send(self, raw: str) -> None:
        self.messages.append(json.loads(raw))
        self.changed.set()

    async def wait_for_messages(self, count: int) -> None:
        while len(self.messages) < count:
            self.changed.clear()
            await asyncio.wait_for(self.changed.wait(), timeout=2)


async def test_node_relay_tunnel_dispatches_existing_gateway_asgi_surface(
    tmp_path: Path,
) -> None:
    def clock() -> float:
        return 1000.0

    hub_repository = HubRepository(tmp_path / "hub.db", hub_id="hub-1", clock=clock)
    hub = HubService(
        hub_repository,
        tmp_path / "hub.key",
        owner_token="o" * 43,
        clock=clock,
    )
    node = NodeIdentityStore(tmp_path / "node.json", clock=clock).load_or_create()
    enrollment_grant = hub_repository.create_enrollment_grant()
    enrollment_transcript = {
        "audience": "knoa-node-enrollment-v1",
        "hub_id": "hub-1",
        "grant_id": enrollment_grant.grant_id,
        "challenge": enrollment_grant.challenge,
        "node_id": node.node_id,
        "signing_public_key": node.signing_public_key,
        "signing_key_version": 1,
        "configuration_public_key": node.configuration_public_key,
        "configuration_key_version": 1,
    }
    hub.enroll_node({
        **enrollment_transcript,
        "grant_secret": enrollment_grant.secret,
        "display_name": "Desktop",
        "platform": "linux",
        "version": "1",
        "signature": node.sign(canonical_json(enrollment_transcript)),
    })

    app_key = Ed25519PrivateKey.generate()
    app_public = _public_key(app_key)
    hub_repository.register_installation("subject_owner", "app-1", app_public, "Phone")
    devices = GatewayIdentityRepository(tmp_path / "gateway.db", clock=clock)
    pairing = devices.create_pairing_grant("subject_owner")
    device = devices.register_verified_device(
        pairing.grant_id,
        pairing.secret,
        display_name="Phone",
        public_key=app_public,
    )
    ticket = hub.issue_ticket("app-1", node.node_id, "relay")
    claims = json.loads(decode_base64url(ticket.partition(".")[0]))
    client_ephemeral = X25519PrivateKey.generate()
    client_nonce = encode_base64url(b"c" * 24)
    client_ephemeral_public = encode_base64url(
        client_ephemeral.public_key().public_bytes(
            serialization.Encoding.Raw,
            serialization.PublicFormat.Raw,
        )
    )
    unsigned = {
        "audience": "knoa-node-client-hello-v1",
        "version": 1,
        "ticket": ticket,
        "installation_id": "app-1",
        "device_id": device.device_id,
        "client_signing_public_key": app_public,
        "client_ephemeral_public_key": client_ephemeral_public,
        "client_nonce": client_nonce,
        "transport": "relay",
    }
    hello = ClientHello(
        ticket=ticket,
        installation_id="app-1",
        device_id=device.device_id,
        client_signing_public_key=app_public,
        client_ephemeral_public_key=client_ephemeral_public,
        client_nonce=client_nonce,
        signature=encode_base64url(app_key.sign(canonical_json(unsigned))),
    )
    manager = NodeRelayManager(
        store=NodeHubStore(tmp_path / "node-hub.json"),
        identity=node,
        identities=devices,
        app=Starlette(routes=[Route("/echo", _echo, methods=["POST"])]),
        clock=clock,
    )
    enrollment = NodeHubEnrollment(
        hub_url="https://hub.example.com",
        hub_id="hub-1",
        hub_signing_public_key=hub.signing_public_key,
        enrolled_at=1000,
    )
    websocket = _WebSocket()
    sessions = {}
    session_id = claims["ticket_id"]

    await manager._receive_frame(  # noqa: SLF001 - protocol integration boundary
        websocket,
        enrollment,
        sessions,
        _frame(session_id, 0, 0, hello.model_dump_json().encode()),
    )
    server_frame = RelayFrame.model_validate(websocket.messages[0]["frame"])
    server_hello = json.loads(decode_base64url(server_frame.ciphertext))
    shared = client_ephemeral.exchange(
        X25519PublicKey.from_public_bytes(
            decode_base64url(server_hello["server_ephemeral_public_key"])
        )
    )
    client_to_node, node_to_client = derive_session_keys(
        shared,
        ticket_id=session_id,
        client_nonce=client_nonce,
        server_nonce=server_hello["server_nonce"],
    )
    encryptor = ChaCha20Poly1305(client_to_node)

    def encrypted(sequence: int, message: dict) -> bytes:
        return encryptor.encrypt(
            b"C2N1" + sequence.to_bytes(8, "big"),
            canonical_json(message),
            _aad(session_id, "client_to_node", sequence),
        )

    body = b"hello relay"
    await manager._receive_frame(  # noqa: SLF001
        websocket,
        enrollment,
        sessions,
        _frame(session_id, 1, 0, encrypted(0, {
            "type": "request_start",
            "method": "POST",
            "path": "/echo",
            "headers": {"content-type": "text/plain"},
            "body_length": len(body),
        })),
    )
    await manager._receive_frame(  # noqa: SLF001
        websocket,
        enrollment,
        sessions,
        _frame(session_id, 1, 1, encrypted(1, {
            "type": "request_body",
            "data": base64.urlsafe_b64encode(body).rstrip(b"=").decode(),
        })),
    )
    await manager._receive_frame(  # noqa: SLF001
        websocket,
        enrollment,
        sessions,
        _frame(session_id, 1, 2, encrypted(2, {"type": "request_end"})),
    )
    await websocket.wait_for_messages(4)

    decryptor = ChaCha20Poly1305(node_to_client)
    responses = []
    for wrapped in websocket.messages[1:]:
        frame = RelayFrame.model_validate(wrapped["frame"])
        responses.append(json.loads(decryptor.decrypt(
            b"N2C1" + frame.sequence.to_bytes(8, "big"),
            decode_base64url(frame.ciphertext),
            _aad(session_id, "node_to_client", frame.sequence),
        )))

    assert responses[0]["status"] == 201
    assert responses[0]["headers"]["x-knoa-sha256"] == "digest-a"
    assert decode_base64url(responses[1]["data"]) == body
    assert responses[2] == {"type": "response_end"}
