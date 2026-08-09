from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest

from pc_assistant.config import AppConfig
from pc_assistant.gateway.adapter import SecureGatewayAdapter
from pc_assistant.gateway.identity import PairingGrantRejectedError


def _config(tmp_path) -> AppConfig:
    return AppConfig(
        fallback_enabled=False,
        runtime_root=str(tmp_path),
        gateway_enabled=True,
        gateway_host="127.0.0.1",
        gateway_port=0,
        gateway_session_ttl_seconds=900,
    )


class _Authentication:
    def begin_pairing(self, grant_id):
        assert grant_id == "pgr-a"
        return SimpleNamespace(challenge_id="gch-a", nonce="n" * 43, expires_at=2.0)

    def complete_pairing(self, **kwargs):
        assert kwargs["grant_secret"] == "s" * 43
        return SimpleNamespace(device_id="dev-a", principal_id="personal:owner")

    def begin_authentication(self, device_id):
        assert device_id == "dev-a"
        return SimpleNamespace(challenge_id="gch-b", nonce="m" * 43, expires_at=3.0)

    def complete_authentication(self, **kwargs):
        assert kwargs["session_ttl_seconds"] == 900
        return SimpleNamespace(
            token="v1.gws-a." + "t" * 43,
            expires_at=900.0,
            device_id="dev-a",
        )

    def authenticate_session(self, token):
        assert token == "v1.gws-a." + "t" * 43
        return SimpleNamespace(
            session_id="gws-a",
            expires_at=900.0,
            device=SimpleNamespace(
                device_id="dev-a",
                principal_id="personal:owner",
            ),
        )


@pytest.mark.asyncio
async def test_gateway_adapter_exposes_bounded_authentication_flow(tmp_path) -> None:
    adapter = SecureGatewayAdapter(
        _config(tmp_path),
        authentication=_Authentication(),
    )
    transport = httpx.ASGITransport(app=adapter.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://gateway.local") as http:
        pair = await http.post("/v1/pair/challenge", json={"grant_id": "pgr-a"})
        auth = await http.post("/v1/auth/challenge", json={"device_id": "dev-a"})
        complete = await http.post(
            "/v1/auth/complete",
            json={
                "device_id": "dev-a",
                "challenge_id": "gch-b",
                "nonce": "m" * 43,
                "signature": "x" * 86,
            },
        )
        session = await http.get(
            "/v1/session",
            headers={"Authorization": "Bearer " + complete.json()["token"]},
        )

    assert pair.status_code == 200
    assert pair.json()["challenge_id"] == "gch-a"
    assert auth.json()["challenge_id"] == "gch-b"
    assert complete.status_code == 200
    assert session.json()["principal_id"] == "personal:owner"


@pytest.mark.asyncio
async def test_gateway_adapter_rejects_unbounded_or_extra_json(tmp_path) -> None:
    adapter = SecureGatewayAdapter(_config(tmp_path), authentication=_Authentication())
    transport = httpx.ASGITransport(app=adapter.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://gateway.local") as http:
        extra = await http.post(
            "/v1/auth/challenge",
            json={"device_id": "dev-a", "principal_id": "attacker"},
        )
        oversized = await http.post(
            "/v1/auth/challenge",
            content=b"x" * (16 * 1024 + 1),
            headers={"Content-Type": "application/json"},
        )

    assert extra.status_code == 400
    assert oversized.status_code == 413


@pytest.mark.asyncio
async def test_gateway_adapter_bounds_requests_and_rejects_unknown_grants(tmp_path) -> None:
    class _RejectedAuthentication(_Authentication):
        def begin_pairing(self, grant_id):
            raise PairingGrantRejectedError("unknown grant")

    adapter = SecureGatewayAdapter(
        _config(tmp_path),
        authentication=_RejectedAuthentication(),
    )
    transport = httpx.ASGITransport(app=adapter.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://gateway.local") as http:
        rejected = await http.post("/v1/pair/challenge", json={"grant_id": "missing"})
        responses = [
            await http.post("/v1/auth/challenge", json={"device_id": "dev-a"})
            for _ in range(31)
        ]

    assert rejected.status_code == 401
    assert responses[-1].status_code == 429


def test_gateway_adapter_refuses_non_loopback_binding(tmp_path) -> None:
    with pytest.raises(ValueError, match="loopback"):
        SecureGatewayAdapter(
            _config(tmp_path).model_copy(update={"gateway_host": "0.0.0.0"}),
            authentication=_Authentication(),
        )


@pytest.mark.asyncio
async def test_gateway_adapter_embedded_http_lifecycle(tmp_path) -> None:
    adapter = SecureGatewayAdapter(_config(tmp_path), authentication=_Authentication())
    await adapter.start()
    try:
        async with httpx.AsyncClient(trust_env=False) as http:
            response = await http.get(f"http://127.0.0.1:{adapter.bound_port}/health")
        assert response.json() == {"status": "ok", "scope": "authentication"}
    finally:
        await adapter.stop()
