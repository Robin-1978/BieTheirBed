from knoa_platform.transport_health import TransportHealth
from knoa_platform.transport_middleware import TransportHealthMiddleware
import httpx
import pytest
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route


def test_transport_health_keeps_priority_and_stage_metrics() -> None:
    health = TransportHealth()
    health.record("p2p", "discovery", ok=True)
    health.record("p2p", "verification", ok=True)
    health.record("relay", "verification", ok=False, error=" timeout ")
    health.activate("p2p", reason="mDNS not found")
    snapshot = health.snapshot()
    assert snapshot["preferred_order"] == ["mdns", "p2p", "relay"]
    assert snapshot["active"] == "p2p"
    assert snapshot["discovery_success"]["p2p"] == 1
    assert snapshot["last_error"]["relay"] == "timeout"


def test_transport_health_poll_observations_are_idempotent_until_recovery() -> None:
    health = TransportHealth()
    health.observe("mdns", "discovery", ok=True)
    health.observe("mdns", "discovery", ok=True)
    assert health.snapshot()["discovery_success"]["mdns"] == 1

    health.observe("mdns", "discovery", ok=False, error="firewall")
    health.observe("mdns", "discovery", ok=True)
    assert health.snapshot()["discovery_success"]["mdns"] == 2


@pytest.mark.asyncio
async def test_transport_health_middleware_records_completed_request() -> None:
    health = TransportHealth()

    async def endpoint(_request):
        return JSONResponse({"ok": True})

    app = Starlette(routes=[Route("/health", endpoint)])
    app.add_middleware(TransportHealthMiddleware, health=health)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://node",
    ) as client:
        response = await client.get("/health", headers={"X-Knoa-Transport": "mdns"})

    assert response.status_code == 200
    snapshot = health.snapshot()
    assert snapshot["active"] == "mdns"
    assert snapshot["verification_success"]["mdns"] == 1
    assert snapshot["request_success"]["mdns"] == 1
