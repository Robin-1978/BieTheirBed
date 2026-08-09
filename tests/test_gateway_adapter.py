from __future__ import annotations

import base64
import ipaddress
import os
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

os.environ.setdefault("CRYPTOGRAPHY_OPENSSL_NO_LEGACY", "1")

import httpx
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from pc_assistant.config import AppConfig
from pc_assistant.agent_runtime.contracts import (
    ArtifactDownloadResult,
    ArtifactTranscriptionResult,
    ExtensionStatusRecord,
    RuntimeStatus,
    ToolDescriptorRecord,
    ToolListResult,
)
from pc_assistant.artifacts import ArtifactRef
from pc_assistant.gateway.adapter import SecureGatewayAdapter
from pc_assistant.gateway.auth import GatewayAuthenticationRejectedError
from pc_assistant.gateway.identity import PairingGrantRejectedError
from pc_assistant.service.core_api import TaskSnapshot
from pc_assistant.tasks import (
    ApprovalState,
    PrincipalTaskEvent,
    TaskCancelResult,
    TaskEvent,
    TaskEventPayload,
    TaskPauseResult,
    TaskState,
)


def _config(tmp_path) -> AppConfig:
    return AppConfig(
        fallback_enabled=False,
        runtime_root=str(tmp_path),
        gateway_enabled=True,
        gateway_host="127.0.0.1",
        gateway_port=0,
        gateway_session_ttl_seconds=900,
    )


def _tls_config(tmp_path) -> AppConfig:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    now = datetime.now(UTC)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(
            x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )
    cert_path = tmp_path / "gateway-cert.pem"
    key_path = tmp_path / "gateway-key.pem"
    cert_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    cert_path.chmod(0o600)
    key_path.chmod(0o600)
    return _config(tmp_path).model_copy(
        update={
            "gateway_remote_enabled": True,
            "gateway_tls_cert_file": str(cert_path),
            "gateway_tls_key_file": str(key_path),
        }
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
            principal_id="personal:owner",
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


def _task_snapshot() -> TaskSnapshot:
    return TaskSnapshot(
        task_id="task-a",
        session_handle="session-a",
        client_request_id="request-a",
        goal="hello",
        tools_enabled=True,
        priority=0,
        state=TaskState.RUNNING,
        phase="working",
        attempt_count=1,
        cancel_requested=False,
        created_at=1.0,
        updated_at=2.0,
        next_event_seq=3,
    )


class _Core:
    def __init__(self) -> None:
        self.calls = []
        self.closed = False

    async def close(self):
        self.closed = True

    async def create_session(self, principal_id):
        self.calls.append(("create_session", principal_id))
        return "session-a"

    async def create_task(self, principal_id, session_handle, user_input, attachments, **kwargs):
        self.calls.append(
            ("create_task", principal_id, session_handle, user_input, attachments, kwargs)
        )
        return SimpleNamespace(task_id="task-a", state=TaskState.QUEUED)

    async def list_tasks(self, principal_id, **kwargs):
        self.calls.append(("list_tasks", principal_id, kwargs))
        return SimpleNamespace(tasks=(_task_snapshot(),), next_cursor="next-a")

    async def get_task(self, principal_id, task_id):
        self.calls.append(("get_task", principal_id, task_id))
        return _task_snapshot()

    async def cancel_task(self, principal_id, task_id, *, reason):
        self.calls.append(("cancel_task", principal_id, task_id, reason))
        return SimpleNamespace(
            result=TaskCancelResult(accepted=True, state=TaskState.CANCELLED)
        )

    async def pause_task(self, principal_id, task_id, *, reason):
        self.calls.append(("pause_task", principal_id, task_id, reason))
        return SimpleNamespace(
            result=TaskPauseResult(accepted=True, state=TaskState.PAUSED)
        )

    async def resume_task(
        self,
        principal_id,
        task_id,
        *,
        reason,
        acknowledge_outcome_unknown,
    ):
        self.calls.append(
            (
                "resume_task",
                principal_id,
                task_id,
                reason,
                acknowledge_outcome_unknown,
            )
        )
        return SimpleNamespace(task_id=task_id, state=TaskState.QUEUED)

    async def retry_task(self, principal_id, task_id, *, reason):
        self.calls.append(("retry_task", principal_id, task_id, reason))
        return SimpleNamespace(task_id="task-retry", state=TaskState.QUEUED)

    async def transcribe_artifact(self, principal_id, session_handle, artifact_id):
        self.calls.append(
            ("transcribe_artifact", principal_id, session_handle, artifact_id)
        )
        return ArtifactTranscriptionResult(
            artifact_id=artifact_id,
            transcript="会议结论",
            tool_name="speech_to_text",
        )

    async def status(self, principal_id, session_handle):
        self.calls.append(("status", principal_id, session_handle))
        return RuntimeStatus(
            status="ready",
            connected=True,
            details={"total_tokens": 42},
            extensions=(
                ExtensionStatusRecord(
                    extension_id="jira",
                    kind="skill",
                    state="configured",
                ),
            ),
        )

    async def list_tools(self, principal_id, session_handle):
        self.calls.append(("list_tools", principal_id, session_handle))
        descriptor = ToolDescriptorRecord(
            name="web_search",
            description="Search the web",
            origin_kind="builtin",
            extension_id="builtin",
            effect="read_only",
            risk="low",
            requires_confirmation=False,
        )
        return ToolListResult(tools=(descriptor.name,), descriptors=(descriptor,))

    async def resolve_approval(self, principal_id, approval_id, *, approved):
        self.calls.append(
            ("resolve_approval", principal_id, approval_id, approved)
        )
        return SimpleNamespace(
            approval_id=approval_id,
            resolved=True,
            state=ApprovalState.APPROVED,
        )

    async def principal_task_events(self, principal_id, *, after_id):
        self.calls.append(("principal_task_events", principal_id, after_id))
        yield PrincipalTaskEvent(
            feed_event_id=after_id + 1,
            principal_id=principal_id,
            event=TaskEvent(
                task_id="task-a",
                event_seq=3,
                event_type="content_delta",
                payload=TaskEventPayload(content="你好"),
                occurred_at=3.0,
            ),
        )

    async def task_events(self, principal_id, task_id, *, after_seq):
        self.calls.append(("task_events", principal_id, task_id, after_seq))
        return (
            TaskEvent(
                task_id=task_id,
                event_seq=after_seq + 1,
                event_type="reasoning_delta",
                payload=TaskEventPayload(content="分析中"),
                occurred_at=3.0,
            ),
        )

    async def upload_artifact(
        self,
        principal_id,
        session_handle,
        data_url,
        *,
        media_type,
        name,
        caption,
    ):
        self.calls.append(
            (
                "upload_artifact",
                principal_id,
                session_handle,
                data_url,
                media_type,
                name,
                caption,
            )
        )
        return ArtifactRef(
            artifact_id="artifact-a",
            kind="file",
            name=name or "note.txt",
            media_type=media_type,
            size=5,
            direction="inbound",
            ownership="managed",
        )

    async def download_artifact(self, principal_id, session_handle, artifact_id):
        self.calls.append(
            ("download_artifact", principal_id, session_handle, artifact_id)
        )
        artifact = ArtifactRef(
            artifact_id=artifact_id,
            kind="file",
            name="报告.txt",
            media_type="text/plain",
            size=5,
        )
        return ArtifactDownloadResult(
            artifact=artifact,
            data_url="data:text/plain;base64," + base64.b64encode(b"hello").decode(),
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
async def test_gateway_adapter_exposes_only_principal_scoped_core_commands(tmp_path) -> None:
    core = _Core()
    adapter = SecureGatewayAdapter(
        _config(tmp_path),
        authentication=_Authentication(),
        core=core,
    )
    transport = httpx.ASGITransport(app=adapter.app)
    headers = {"Authorization": "Bearer " + "v1.gws-a." + "t" * 43}
    async with httpx.AsyncClient(transport=transport, base_url="http://gateway.local") as http:
        session = await http.post("/v1/sessions", headers=headers)
        created = await http.post(
            "/v1/tasks",
            headers=headers,
            json={"session_handle": "session-a", "input": "hello"},
        )
        listed = await http.get(
            "/v1/tasks?state=running&limit=10",
            headers=headers,
        )
        detail = await http.get("/v1/tasks/task-a", headers=headers)
        timeline = await http.get(
            "/v1/tasks/task-a/events?after_seq=2",
            headers=headers,
        )
        cancelled = await http.post(
            "/v1/tasks/task-a/cancel",
            headers=headers,
            json={"reason": "owner request"},
        )
        paused = await http.post(
            "/v1/tasks/task-a/pause",
            headers=headers,
            json={"reason": "later"},
        )
        resumed = await http.post(
            "/v1/tasks/task-a/resume",
            headers=headers,
            json={"reason": "continue", "acknowledge_outcome_unknown": True},
        )
        retried = await http.post(
            "/v1/tasks/task-a/retry",
            headers=headers,
            json={"reason": "try again"},
        )
        approval = await http.post(
            "/v1/approvals/approval-a/resolve",
            headers=headers,
            json={"approved": True},
        )

    assert session.status_code == 201
    assert session.json() == {"session_handle": "session-a"}
    assert created.status_code == 202
    assert created.json() == {"task_id": "task-a", "state": "queued"}
    assert listed.json()["tasks"][0]["task_id"] == "task-a"
    assert listed.json()["next_cursor"] == "next-a"
    assert detail.json()["task"]["phase"] == "working"
    assert timeline.json()["events"][0]["event_seq"] == 3
    assert timeline.json()["events"][0]["payload"]["content"] == "分析中"
    assert cancelled.json() == {"accepted": True, "state": "cancelled"}
    assert paused.json() == {"accepted": True, "state": "paused"}
    assert resumed.json() == {"accepted": True, "state": "queued"}
    assert retried.status_code == 202
    assert retried.json() == {"task_id": "task-retry", "state": "queued"}
    assert approval.json() == {
        "approval_id": "approval-a",
        "resolved": True,
        "state": "approved",
    }
    assert {call[1] for call in core.calls} == {"personal:owner"}


@pytest.mark.asyncio
async def test_gateway_adapter_exposes_transcription_runtime_and_tools(tmp_path) -> None:
    core = _Core()
    adapter = SecureGatewayAdapter(
        _config(tmp_path),
        authentication=_Authentication(),
        core=core,
    )
    transport = httpx.ASGITransport(app=adapter.app)
    headers = {"Authorization": "Bearer " + "v1.gws-a." + "t" * 43}
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://gateway.local",
    ) as http:
        transcription = await http.post(
            "/v1/artifacts/artifact-a/transcribe",
            params={"session_handle": "session-a"},
            headers=headers,
        )
        status = await http.get(
            "/v1/runtime/status",
            params={"session_handle": "session-a"},
            headers=headers,
        )
        tools = await http.get(
            "/v1/tools",
            params={"session_handle": "session-a"},
            headers=headers,
        )

    assert transcription.json()["result"]["transcript"] == "会议结论"
    assert status.json()["result"]["details"]["total_tokens"] == 42
    assert status.json()["result"]["extensions"][0]["extension_id"] == "jira"
    assert tools.json()["result"]["descriptors"][0]["origin_kind"] == "builtin"


@pytest.mark.asyncio
async def test_gateway_adapter_exposes_current_device_audit_without_secrets(
    tmp_path,
) -> None:
    adapter = SecureGatewayAdapter(
        _config(tmp_path),
        authentication=_Authentication(),
        core=_Core(),
    )
    transport = httpx.ASGITransport(app=adapter.app)
    token = "v1.gws-a." + "t" * 43
    headers = {"Authorization": "Bearer " + token}
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://gateway.local",
    ) as http:
        await http.get("/v1/session", headers=headers)
        response = await http.get("/v1/device/audit", headers=headers)

    assert response.status_code == 200
    payload = response.json()
    assert payload["events"]
    assert {event["event_type"] for event in payload["events"]} == {"command"}
    serialized = response.text
    assert token not in serialized
    assert "personal:owner" not in serialized
    assert "dev-a" not in serialized


@pytest.mark.asyncio
async def test_gateway_adapter_rejects_unauthenticated_core_commands(tmp_path) -> None:
    core = _Core()
    adapter = SecureGatewayAdapter(
        _config(tmp_path),
        authentication=_Authentication(),
        core=core,
    )
    transport = httpx.ASGITransport(app=adapter.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://gateway.local") as http:
        response = await http.get("/v1/tasks")

    assert response.status_code == 401
    assert core.calls == []


@pytest.mark.asyncio
async def test_gateway_adapter_streams_resumable_standard_task_events(tmp_path) -> None:
    core = _Core()
    adapter = SecureGatewayAdapter(
        _config(tmp_path),
        authentication=_Authentication(),
        core=core,
    )
    transport = httpx.ASGITransport(app=adapter.app)
    headers = {
        "Authorization": "Bearer " + "v1.gws-a." + "t" * 43,
        "Last-Event-ID": "40",
    }
    async with httpx.AsyncClient(transport=transport, base_url="http://gateway.local") as http:
        response = await http.get("/v1/events", headers=headers)

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert "id: 41\n" in response.text
    assert "event: content_delta\n" in response.text
    assert '"content":"你好"' in response.text
    assert core.calls == [("principal_task_events", "personal:owner", 40)]


@pytest.mark.asyncio
async def test_gateway_event_stream_stops_when_device_session_is_revoked(tmp_path) -> None:
    class _RevokedAuthentication(_Authentication):
        def __init__(self) -> None:
            self.calls = 0

        def authenticate_session(self, token):
            self.calls += 1
            if self.calls > 1:
                raise GatewayAuthenticationRejectedError("revoked")
            return super().authenticate_session(token)

    authentication = _RevokedAuthentication()
    adapter = SecureGatewayAdapter(
        _config(tmp_path),
        authentication=authentication,
        core=_Core(),
    )
    transport = httpx.ASGITransport(app=adapter.app)
    headers = {"Authorization": "Bearer " + "v1.gws-a." + "t" * 43}
    async with httpx.AsyncClient(transport=transport, base_url="http://gateway.local") as http:
        response = await http.get("/v1/events", headers=headers)

    assert response.status_code == 200
    assert response.content == b""
    assert authentication.calls == 2


@pytest.mark.asyncio
async def test_gateway_adapter_transfers_bounded_binary_artifacts(tmp_path) -> None:
    core = _Core()
    adapter = SecureGatewayAdapter(
        _config(tmp_path),
        authentication=_Authentication(),
        core=core,
    )
    transport = httpx.ASGITransport(app=adapter.app)
    headers = {"Authorization": "Bearer " + "v1.gws-a." + "t" * 43}
    async with httpx.AsyncClient(transport=transport, base_url="http://gateway.local") as http:
        uploaded = await http.post(
            "/v1/artifacts",
            params={
                "session_handle": "session-a",
                "name": "note.txt",
                "caption": "sample",
            },
            headers={**headers, "Content-Type": "text/plain"},
            content=b"hello",
        )
        downloaded = await http.get(
            "/v1/artifacts/artifact-a",
            params={"session_handle": "session-a"},
            headers=headers,
        )

    assert uploaded.status_code == 201
    assert uploaded.json()["artifact"]["artifact_id"] == "artifact-a"
    upload_call = next(call for call in core.calls if call[0] == "upload_artifact")
    assert upload_call[1:3] == ("personal:owner", "session-a")
    assert upload_call[3] == "data:text/plain;base64,aGVsbG8="
    assert downloaded.status_code == 200
    assert downloaded.content == b"hello"
    assert downloaded.headers["content-type"] == "text/plain; charset=utf-8"
    assert "filename*=UTF-8''%E6%8A%A5%E5%91%8A.txt" in downloaded.headers[
        "content-disposition"
    ]


@pytest.mark.asyncio
async def test_gateway_adapter_rejects_oversized_artifact_before_core(tmp_path) -> None:
    core = _Core()
    config = _config(tmp_path).model_copy(
        update={"gateway_artifact_max_bytes": 1024 * 1024}
    )
    adapter = SecureGatewayAdapter(
        config,
        authentication=_Authentication(),
        core=core,
    )
    transport = httpx.ASGITransport(app=adapter.app)
    headers = {
        "Authorization": "Bearer " + "v1.gws-a." + "t" * 43,
        "Content-Type": "application/octet-stream",
    }
    async with httpx.AsyncClient(transport=transport, base_url="http://gateway.local") as http:
        response = await http.post(
            "/v1/artifacts",
            params={"session_handle": "session-a"},
            headers=headers,
            content=b"x" * (1024 * 1024 + 1),
        )

    assert response.status_code == 413
    assert core.calls == []


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


@pytest.mark.asyncio
async def test_gateway_adapter_serves_tls_when_remote_mode_is_explicit(tmp_path) -> None:
    adapter = SecureGatewayAdapter(
        _tls_config(tmp_path),
        authentication=_Authentication(),
    )
    await adapter.start()
    try:
        async with httpx.AsyncClient(verify=False, trust_env=False) as http:
            response = await http.get(f"https://127.0.0.1:{adapter.bound_port}/health")
        assert response.json() == {"status": "ok", "scope": "authentication"}
    finally:
        await adapter.stop()


def test_gateway_adapter_requires_owner_only_tls_private_key(tmp_path) -> None:
    config = _tls_config(tmp_path)
    key_path = tmp_path / "gateway-key.pem"
    key_path.chmod(0o640)

    with pytest.raises(ValueError, match="owner-only"):
        SecureGatewayAdapter(config, authentication=_Authentication())
