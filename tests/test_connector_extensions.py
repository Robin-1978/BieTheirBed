from __future__ import annotations

import asyncio
import json
import stat
from pathlib import Path

import pytest
import httpx
from pydantic import ValidationError

from pc_assistant.agent_runtime.composition import build_core_runtime
from pc_assistant.agent_runtime.contracts import RuntimeScope
from pc_assistant.agent_runtime.tool_step import (
    ProposedToolCall,
    ToolArgumentPolicy,
    ToolStep,
    ToolStepContext,
)
from pc_assistant.config import AppConfig
from pc_assistant.extensions import ExtensionManager, ExtensionState
from pc_assistant.extensions.connector import (
    ConnectorAuditRecorder,
    ConnectorAuthorizationError,
    YuqueHTTPClient,
    YuqueConnectorProvider,
)
from pc_assistant.extensions.models import YuqueConnectorConfig
from pc_assistant.extensions.secrets import (
    PrivateFileSecretStore,
    SecretUnavailableError,
    SecretValue,
)
from pc_assistant.tools.base import ToolCapability, ToolOriginKind
from pc_assistant.tools.registry import ToolRegistry


class _Secrets:
    def __init__(self, value: str = "private-token") -> None:
        self.value = value
        self.requested: list[str] = []

    def resolve(self, secret_id: str) -> SecretValue:
        self.requested.append(secret_id)
        return SecretValue(self.value)


class _YuqueClient:
    def __init__(self, *, authorization_failure: bool = False) -> None:
        self.authorization_failure = authorization_failure
        self.started = False
        self.closed = False
        self.calls: list[tuple] = []

    async def start(self) -> None:
        self.started = True

    async def get_document(self, namespace: str, identifier: str):
        self.calls.append(("get", namespace, identifier))
        if self.authorization_failure:
            raise ConnectorAuthorizationError("expired private-token")
        return {"title": "Document", "body": "Hello"}

    async def update_document(
        self,
        namespace: str,
        identifier: str,
        *,
        title: str,
        body: str,
    ):
        self.calls.append(("update", namespace, identifier, title, body))
        return {"id": identifier, "title": title}

    async def close(self) -> None:
        self.closed = True


class _Confirmation:
    def __init__(self, approved: bool = True) -> None:
        self.approved = approved
        self.calls = 0

    async def confirm(self, scope, run_id, call, reason: str) -> bool:
        del scope, run_id, call, reason
        self.calls += 1
        return self.approved


def _config() -> YuqueConnectorConfig:
    return YuqueConnectorConfig(
        enabled=True,
        token_secret="yuque-primary",
    )


def _context(
    capabilities: frozenset[ToolCapability],
    confirmation: _Confirmation | None = None,
) -> ToolStepContext:
    return ToolStepContext(
        scope=RuntimeScope(principal_id="local", session_handle="session-a"),
        run_id="run-a",
        client_request_id="request-a",
        capabilities=capabilities,
        cancellation=asyncio.Event(),
        confirmation=confirmation,
    )


def test_private_secret_store_uses_env_or_owner_only_file(tmp_path: Path) -> None:
    store = PrivateFileSecretStore(
        tmp_path,
        environment={"PC_SECRET_YUQUE_PRIMARY": "env-token"},
    )
    assert store.resolve("yuque-primary").reveal() == "env-token"
    assert "env-token" not in repr(store.resolve("yuque-primary"))

    path = tmp_path / "file-token.secret"
    path.write_text("file-value\n", encoding="utf-8")
    path.chmod(0o600)
    file_store = PrivateFileSecretStore(tmp_path, environment={})
    assert file_store.resolve("file-token").reveal() == "file-value"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_private_secret_store_rejects_exposed_or_missing_secret(tmp_path: Path) -> None:
    exposed = tmp_path / "exposed.secret"
    exposed.write_text("must-not-leak", encoding="utf-8")
    exposed.chmod(0o644)
    store = PrivateFileSecretStore(tmp_path, environment={})

    with pytest.raises(SecretUnavailableError, match="0600") as exposed_error:
        store.resolve("exposed")
    with pytest.raises(SecretUnavailableError, match="unavailable"):
        store.resolve("missing")
    assert "must-not-leak" not in str(exposed_error.value)


def test_connector_config_accepts_secret_reference_not_literal_credential() -> None:
    with pytest.raises(ValidationError, match="safe Secret ID") as exc_info:
        YuqueConnectorConfig(
            enabled=True,
            token_secret="literal.token.value",
        )
    assert "literal.token.value" not in str(exc_info.value)


@pytest.mark.asyncio
async def test_yuque_connector_registers_read_write_tools_with_standard_policy(
    tmp_path: Path,
) -> None:
    client = _YuqueClient()
    secrets = _Secrets()
    provider = YuqueConnectorProvider(
        "yuque",
        _config(),
        secrets,
        ConnectorAuditRecorder(tmp_path / "connector-audit.jsonl"),
        client_factory=lambda *_args: client,
    )
    registry = ToolRegistry()
    manager = ExtensionManager(registry, (provider,))
    await manager.start()

    assert manager.statuses[0].state is ExtensionState.RUNNING
    assert registry.list_tools() == [
        "connector__yuque__get_document",
        "connector__yuque__update_document",
    ]
    assert registry.origin("connector__yuque__get_document").kind is ToolOriginKind.CONNECTOR
    descriptors = {
        item.name: item
        for item in registry.descriptors_for(
            frozenset({ToolCapability.NETWORK, ToolCapability.CONNECTOR})
        )
    }
    assert descriptors["connector__yuque__get_document"].requires_confirmation is False
    assert descriptors["connector__yuque__update_document"].requires_confirmation is True
    assert (
        descriptors["connector__yuque__update_document"].origin.extension_id
        == "connector:yuque"
    )
    assert secrets.requested == ["yuque-primary"]

    step = ToolStep(registry, ToolArgumentPolicy(tmp_path))
    read_call = ProposedToolCall(
        call_id="read-a",
        name="connector__yuque__get_document",
        arguments={"namespace": "team/repo", "identifier": "intro"},
    )
    denied = await step.execute(
        _context(frozenset({ToolCapability.NETWORK})),
        read_call,
    )
    traversal = await step.execute(
        _context(
            frozenset({ToolCapability.NETWORK, ToolCapability.CONNECTOR})
        ),
        read_call.model_copy(
            update={"arguments": {"namespace": "../repo", "identifier": "intro"}}
        ),
    )
    completed = await step.execute(
        _context(
            frozenset({ToolCapability.NETWORK, ToolCapability.CONNECTOR})
        ),
        read_call,
    )
    assert denied.code == "capability_denied"
    assert traversal.code == "tool_invalid_arguments"
    assert completed.output["title"] == "Document"

    update_call = ProposedToolCall(
        call_id="write-a",
        name="connector__yuque__update_document",
        arguments={
            "namespace": "team/repo",
            "identifier": "intro",
            "title": "Updated",
            "body": "New body",
        },
    )
    confirmation = _Confirmation()
    updated = await step.execute(
        _context(
            frozenset({ToolCapability.NETWORK, ToolCapability.CONNECTOR}),
            confirmation,
        ),
        update_call,
    )
    assert updated.status == "completed"
    assert confirmation.calls == 1
    assert client.calls[-1] == (
        "update",
        "team/repo",
        "intro",
        "Updated",
        "New body",
    )

    await manager.stop()
    assert client.closed is True
    assert registry.list_tools() == []


@pytest.mark.asyncio
async def test_connector_reports_reauthorization_without_secret_detail(
    tmp_path: Path,
) -> None:
    client = _YuqueClient(authorization_failure=True)
    provider = YuqueConnectorProvider(
        "yuque",
        _config(),
        _Secrets(),
        ConnectorAuditRecorder(tmp_path / "connector-audit.jsonl"),
        client_factory=lambda *_args: client,
    )
    registry = ToolRegistry()
    manager = ExtensionManager(registry, (provider,))
    await manager.start()
    step = ToolStep(registry, ToolArgumentPolicy(tmp_path))
    result = await step.execute(
        _context(
            frozenset({ToolCapability.NETWORK, ToolCapability.CONNECTOR})
        ),
        ProposedToolCall(
            call_id="read-a",
            name="connector__yuque__get_document",
            arguments={"namespace": "team/repo", "identifier": "intro"},
        ),
    )

    assert result.status == "failed"
    assert result.output["code"] == "reauthorization_required"
    assert "private-token" not in result.model_dump_json()
    await manager.stop()


def test_connector_audit_records_metadata_only(tmp_path: Path) -> None:
    path = tmp_path / "connectors.jsonl"
    recorder = ConnectorAuditRecorder(path)
    recorder.record(
        connector_id="yuque",
        operation="update_document",
        outcome="completed",
        status_code=200,
        elapsed_ms=12.34,
    )

    record = json.loads(path.read_text(encoding="utf-8"))
    assert record["operation"] == "update_document"
    assert record["outcome"] == "completed"
    assert "arguments" not in record
    assert "secret" not in path.read_text(encoding="utf-8").casefold()
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


@pytest.mark.asyncio
async def test_missing_connector_secret_is_isolated_from_core_runtime(
    tmp_path: Path,
) -> None:
    composition = build_core_runtime(
        AppConfig(
            fallback_enabled=False,
            runtime_root=str(tmp_path / "runtime"),
            working_directory=str(tmp_path),
            service_port=0,
            connectors={"yuque": _config()},
        )
    )
    await composition.extensions.start()
    try:
        connector_status = next(
            status
            for status in composition.extensions.statuses
            if status.descriptor.extension_id == "connector:yuque"
        )
        assert connector_status.state is ExtensionState.FAILED
        assert "private-token" not in connector_status.detail
        assert "read_file" in composition.registry.list_tools()
    finally:
        await composition.extensions.stop()


@pytest.mark.asyncio
async def test_yuque_http_client_uses_secret_only_at_transport_boundary(
    tmp_path: Path,
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/api/v2/user":
            return httpx.Response(200, json={"data": {"id": 1}})
        if request.method == "GET":
            return httpx.Response(200, json={"data": {"title": "Read"}})
        return httpx.Response(200, json={"data": {"title": "Updated"}})

    audit_path = tmp_path / "connectors.jsonl"
    client = YuqueHTTPClient(
        "yuque",
        YuqueConnectorConfig(
            enabled=True,
            base_url="https://yuque.example.test/api/v2",
            token_secret="yuque-primary",
        ),
        SecretValue("transport-only-token"),
        ConnectorAuditRecorder(audit_path),
        transport=httpx.MockTransport(handler),
    )
    await client.start()
    read = await client.get_document("team/repo", "intro")
    updated = await client.update_document(
        "team/repo",
        "intro",
        title="Updated",
        body="private document body",
    )
    await client.close()

    assert read == {"title": "Read"}
    assert updated == {"title": "Updated"}
    assert [request.method for request in requests] == ["GET", "GET", "PUT"]
    assert all(
        request.headers["x-auth-token"] == "transport-only-token"
        for request in requests
    )
    audit_text = audit_path.read_text(encoding="utf-8")
    assert "transport-only-token" not in audit_text
    assert "private document body" not in audit_text
    assert [json.loads(line)["operation"] for line in audit_text.splitlines()] == [
        "health",
        "get_document",
        "update_document",
    ]
