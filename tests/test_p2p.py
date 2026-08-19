from __future__ import annotations

import pytest
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

import knoa_platform.p2p as p2p_module
from knoa_platform.p2p import P2PClient, P2PServer


@pytest.mark.asyncio
async def test_p2p_data_channel_carries_bounded_http_without_relay(monkeypatch) -> None:
    async def echo(request: Request) -> JSONResponse:
        return JSONResponse({"method": request.method, "body": (await request.body()).decode()})

    monkeypatch.setattr(p2p_module, "_STUN_SERVERS", [])
    server = P2PServer(Starlette(routes=[Route("/echo", echo, methods=["POST"])]))
    client = P2PClient()
    try:
        await client.connect(lambda offer: server.create_answer(sdp=offer["sdp"], kind="app"))
        response = await client.request(
            "POST",
            "/echo",
            headers={"content-type": "text/plain"},
            body=b"hello-p2p",
        )
        assert response.status == 200
        assert response.headers["content-type"].startswith("application/json")
        assert response.body == b'{"method":"POST","body":"hello-p2p"}'
    finally:
        await client.close()
        await server.close()


@pytest.mark.asyncio
async def test_resource_p2p_rejects_non_resource_gateway_paths(monkeypatch) -> None:
    async def health(_request: Request) -> JSONResponse:
        return JSONResponse({"ok": True})

    monkeypatch.setattr(p2p_module, "_STUN_SERVERS", [])
    server = P2PServer(Starlette(routes=[Route("/health", health)]))
    client = P2PClient()
    try:
        await client.connect(lambda offer: server.create_answer(sdp=offer["sdp"], kind="resource"))
        with pytest.raises(ConnectionError, match="reset"):
            await client.request("GET", "/health", timeout=5)
    finally:
        await client.close()
        await server.close()
