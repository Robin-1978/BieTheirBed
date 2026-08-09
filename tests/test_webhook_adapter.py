from __future__ import annotations

import hashlib
import hmac
import json
from types import SimpleNamespace

import httpx
import pytest

from pc_assistant.adapters.webhook import WebhookAdapter
from pc_assistant.automation import TriggerEventState
from pc_assistant.config import AppConfig


_SECRET = "0123456789abcdef0123456789abcdef"


def _config(tmp_path) -> AppConfig:
    return AppConfig(
        fallback_enabled=False,
        runtime_root=str(tmp_path),
        webhook_enabled=True,
        webhook_host="127.0.0.1",
        webhook_port=0,
        webhook_routes={
            "gitlab": {
                "trigger_id": "trigger-a",
                "principal_id": "personal:feishu:user-a",
                "secret": _SECRET,
            }
        },
    )


def _signature(event_id: str, body: bytes) -> str:
    digest = hmac.new(
        _SECRET.encode(),
        event_id.encode("utf-8") + b"\n" + body,
        hashlib.sha256,
    ).hexdigest()
    return f"sha256={digest}"


class _Client:
    is_connected = True

    def __init__(self) -> None:
        self.calls = []
        self.disconnected = False

    async def fire_trigger(self, trigger_id, external_event_id, payload):
        self.calls.append((trigger_id, external_event_id, payload))
        return SimpleNamespace(
            trigger_event_id="trigger-event-a",
            state=TriggerEventState.RECEIVED,
            task_id="",
        )

    async def disconnect(self) -> None:
        self.disconnected = True
        self.is_connected = False


@pytest.mark.asyncio
async def test_webhook_adapter_verifies_and_forwards_standard_trigger(tmp_path) -> None:
    client = _Client()
    principals = []

    async def factory(principal_id):
        principals.append(principal_id)
        return client

    adapter = WebhookAdapter(_config(tmp_path), client_factory=factory)
    body = json.dumps({"project": "knoa"}, separators=(",", ":")).encode()
    transport = httpx.ASGITransport(app=adapter.app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://webhook.local",
    ) as http:
        response = await http.post(
            "/hooks/gitlab",
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-Knoa-Event-Id": "gitlab-event-1",
                "X-Knoa-Signature": _signature("gitlab-event-1", body),
            },
        )

    assert response.status_code == 202
    assert response.json() == {
        "accepted": True,
        "trigger_event_id": "trigger-event-a",
        "state": "received",
        "task_id": "",
    }
    assert principals == ["personal:feishu:user-a"]
    assert client.calls == [
        ("trigger-a", "gitlab-event-1", {"project": "knoa"})
    ]


@pytest.mark.asyncio
async def test_webhook_adapter_rejects_bad_signature_before_core(tmp_path) -> None:
    called = False

    async def factory(_principal_id):
        nonlocal called
        called = True
        return _Client()

    adapter = WebhookAdapter(_config(tmp_path), client_factory=factory)
    transport = httpx.ASGITransport(app=adapter.app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://webhook.local",
    ) as http:
        response = await http.post(
            "/hooks/gitlab",
            content=b"{}",
            headers={
                "Content-Type": "application/json",
                "X-Knoa-Event-Id": "event-a",
                "X-Knoa-Signature": "sha256=bad",
            },
        )

    assert response.status_code == 401
    assert not called


@pytest.mark.asyncio
async def test_webhook_adapter_bounds_body_and_requires_object_json(tmp_path) -> None:
    adapter = WebhookAdapter(
        _config(tmp_path),
        client_factory=lambda _principal: None,
    )
    transport = httpx.ASGITransport(app=adapter.app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://webhook.local",
    ) as http:
        oversized = b"x" * (128 * 1024 + 1)
        too_large = await http.post(
            "/hooks/gitlab",
            content=oversized,
            headers={
                "Content-Type": "application/json",
                "X-Knoa-Event-Id": "event-large",
                "X-Knoa-Signature": _signature("event-large", oversized),
            },
        )
        body = b"[]"
        invalid_shape = await http.post(
            "/hooks/gitlab",
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-Knoa-Event-Id": "event-array",
                "X-Knoa-Signature": _signature("event-array", body),
            },
        )

    assert too_large.status_code == 413
    assert invalid_shape.status_code == 400


def test_webhook_config_requires_safe_explicit_routes(tmp_path) -> None:
    del tmp_path
    with pytest.raises(ValueError, match="at least one route"):
        AppConfig(webhook_enabled=True)
    with pytest.raises(ValueError, match="safe characters"):
        AppConfig(
            webhook_routes={
                "bad/route": {
                    "trigger_id": "trigger-a",
                    "principal_id": "local",
                    "secret": _SECRET,
                }
            }
        )
    with pytest.raises(ValueError, match="loopback"):
        WebhookAdapter(
            _config(tmp_path=None).model_copy(
                update={"webhook_host": "0.0.0.0"}
            )
        )


@pytest.mark.asyncio
async def test_webhook_adapter_embedded_http_lifecycle(tmp_path) -> None:
    adapter = WebhookAdapter(
        _config(tmp_path),
        client_factory=lambda _principal: None,
    )
    await adapter.start()
    try:
        assert adapter.bound_port is not None
        async with httpx.AsyncClient(trust_env=False) as http:
            response = await http.get(
                f"http://127.0.0.1:{adapter.bound_port}/health"
            )
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}
    finally:
        await adapter.stop()
