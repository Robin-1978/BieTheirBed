from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

import knoa_platform.p2p as p2p_module
from knoa_platform.p2p import P2PClient, P2PServer


def test_missing_aiortc_keeps_node_importable_and_uses_relay_fallback() -> None:
    root = Path(__file__).resolve().parents[1]
    code = textwrap.dedent(
        """
        import asyncio
        import builtins

        original_import = builtins.__import__

        def blocked_import(name, *args, **kwargs):
            if name == "aiortc" or name.startswith("aiortc."):
                raise ModuleNotFoundError("No module named 'aiortc'", name="aiortc")
            return original_import(name, *args, **kwargs)

        builtins.__import__ = blocked_import

        from knoa_platform.p2p import P2PClient, P2PUnavailableError, p2p_available
        import knoa_platform.agent_runtime.composition
        import knoa_platform.gateway.adapter
        import knoa_platform.remote_models

        assert not p2p_available()

        async def verify():
            client = P2PClient()
            try:
                await client.connect(lambda _offer: None)
            except P2PUnavailableError:
                return
            raise AssertionError("P2P unexpectedly started without aiortc")

        asyncio.run(verify())
        """
    )
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(root / "src")

    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=root,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


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
        status = server.status()
        assert status["available"] is True
        assert status["offers_total"] == 1
        assert status["answers_total"] == 1
        assert status["connected_peers"] == 1
        assert status["last_error"] == ""
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


def test_ice_servers_configuration(monkeypatch) -> None:
    from knoa_platform.p2p import build_ice_servers, serialize_ice_servers

    # 1. Explicit configured list
    configured = [
        {"urls": "turn:turn.example.com:3478", "username": "alice", "credential": "secretpassword"}
    ]
    servers = build_ice_servers(configured)
    assert len(servers) == 1
    serialized = serialize_ice_servers(servers)
    assert serialized == [
        {"urls": ["turn:turn.example.com:3478"], "username": "alice", "credential": "secretpassword"}
    ]

    # The config default is an empty list; it must not disable the built-in
    # STUN fallback (the regression in 413a748).
    monkeypatch.delenv("KNOA_ICE_SERVERS", raising=False)
    monkeypatch.delenv("KNOA_TURN_URL", raising=False)
    monkeypatch.setattr(
        p2p_module,
        "_STUN_SERVERS",
        [p2p_module.RTCIceServer(urls="stun:default.example.com:3478")],
    )
    assert serialize_ice_servers(build_ice_servers([])) == [
        {"urls": ["stun:default.example.com:3478"]}
    ]

    # 2. Env variable KNOA_ICE_SERVERS
    monkeypatch.setenv(
        "KNOA_ICE_SERVERS",
        json.dumps([{"urls": "stun:stun.custom.com:3478"}]),
    )
    servers = build_ice_servers()
    assert len(servers) == 1
    assert serialize_ice_servers(servers) == [{"urls": ["stun:stun.custom.com:3478"]}]

    # 3. Env variable KNOA_TURN_URL
    monkeypatch.delenv("KNOA_ICE_SERVERS", raising=False)
    monkeypatch.setenv("KNOA_TURN_URL", "turn:relay.example.com:3478")
    monkeypatch.setenv("KNOA_TURN_USERNAME", "knoa_user")
    monkeypatch.setenv("KNOA_TURN_CREDENTIAL", "knoa_pass")
    servers = build_ice_servers()
    assert len(servers) == 1
    assert serialize_ice_servers(servers) == [
        {"urls": ["turn:relay.example.com:3478"], "username": "knoa_user", "credential": "knoa_pass"}
    ]
