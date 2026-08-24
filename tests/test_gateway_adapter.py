from __future__ import annotations

import base64
import asyncio
import copy
import ipaddress
import os
import shutil
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

os.environ.setdefault("CRYPTOGRAPHY_OPENSSL_NO_LEGACY", "1")

import httpx
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from knoa_platform.agent_runtime.contracts import (
    ArtifactDownloadResult,
    ArtifactTranscriptionResult,
    ExtensionStatusRecord,
    RuntimeStatus,
    ToolDescriptorRecord,
    ToolListResult,
)
from knoa_platform.artifacts import ArtifactRef
from knoa_platform.config import AppConfig
from knoa_platform.configuration import ConfigControlState, ConfigRevision
from knoa_platform.conversation import ChatTurnState
from knoa_platform.gateway.adapter import SecureGatewayAdapter
from knoa_platform.gateway.auth import GatewayAuthenticationRejectedError
from knoa_platform.gateway.identity import PairingGrantRejectedError
from knoa_platform.service.core_api import (
    ChatApprovalSnapshot,
    ChatTurnSnapshot,
    ProductTaskExecutionSnapshot,
    ProductTaskSnapshot,
    TaskSnapshot,
)
from knoa_platform.tasks import (
    ApprovalState,
    PrincipalTaskEvent,
    TaskCancelResult,
    TaskDefinitionState,
    TaskEvent,
    TaskEventPayload,
    TaskLaunchPolicy,
    TaskLaunchReason,
    TaskPauseResult,
    TaskPreflightCheck,
    TaskPreflightResult,
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

    def revoke_device(self, principal_id, device_id):
        assert principal_id == "personal:owner"
        assert device_id == "dev-a"
        return SimpleNamespace(device_id=device_id, principal_id=principal_id)


class _Channels:
    def __init__(self) -> None:
        self.configured = None

    def dingtalk_status(self):
        return {
            "enabled": False,
            "client_id": "",
            "robot_code": "",
            "receive_id": "",
            "client_secret_configured": False,
            "client_secret_rotated_at": 0,
            "running": False,
            "updated_at": 0,
        }

    async def configure_dingtalk(self, **values):
        self.configured = values
        return {
            **self.dingtalk_status(),
            "enabled": values["enabled"],
            "client_id": values["client_id"],
            "client_secret_configured": bool(values["client_secret"]),
            "running": values["enabled"],
            "updated_at": 10,
        }


@pytest.mark.asyncio
async def test_dingtalk_channel_configuration_is_authenticated_and_secret_free(tmp_path) -> None:
    channels = _Channels()
    config = _config(tmp_path)
    adapter = SecureGatewayAdapter(
        config,
        authentication=_Authentication(),
        core=_Core(config),
        channel_controller=channels,
    )
    transport = httpx.ASGITransport(app=adapter.app)
    headers = {"Authorization": "Bearer v1.gws-a." + "t" * 43}
    async with httpx.AsyncClient(transport=transport, base_url="http://node") as http:
        rejected = await http.get("/v1/channels/dingtalk")
        configured = await http.put(
            "/v1/channels/dingtalk",
            headers=headers,
            json={
                "enabled": True,
                "client_id": "ding-client",
                "client_secret": "private-secret",
                "robot_code": "ding-robot",
                "receive_id": "",
            },
        )

    assert rejected.status_code == 401
    assert configured.status_code == 200
    assert configured.json()["channel"]["running"] is True
    assert channels.configured["client_secret"] == "private-secret"
    assert "private-secret" not in configured.text


@pytest.mark.asyncio
async def test_node_profile_update_persists_name_and_returns_it_to_the_app(tmp_path) -> None:
    config = _config(tmp_path)
    adapter = SecureGatewayAdapter(
        config,
        authentication=_Authentication(),
        core=_Core(config),
    )
    adapter._node_hub_store.save(
        hub_url="https://hub.example.test",
        hub_id="hub-a",
        hub_signing_public_key=adapter._node_identity.signing_public_key,
        display_name="Old Name",
    )
    restarts: list[str] = []

    async def restart() -> None:
        restarts.append("restart")

    adapter._node_relay.restart = restart
    transport = httpx.ASGITransport(app=adapter.app)
    headers = {"Authorization": "Bearer v1.gws-a." + "t" * 43}
    async with httpx.AsyncClient(transport=transport, base_url="http://node") as http:
        updated = await http.put(
            "/v1/node",
            headers=headers,
            json={"display_name": "Company Linux"},
        )
        await asyncio.sleep(0.3)

    assert updated.status_code == 200
    assert updated.json()["display_name"] == "Company Linux"
    assert adapter._node_hub_store.load().display_name == "Company Linux"
    assert restarts == ["restart"]


@pytest.mark.asyncio
async def test_node_console_is_loopback_only_and_csrf_protected(tmp_path) -> None:
    config = _config(tmp_path)
    adapter = SecureGatewayAdapter(
        config,
        authentication=_Authentication(),
        core=_Core(config),
    )
    local = httpx.ASGITransport(app=adapter.app, client=("127.0.0.1", 32100))
    remote = httpx.ASGITransport(app=adapter.app, client=("203.0.113.7", 32100))
    async with httpx.AsyncClient(transport=local, base_url="http://node") as http:
        page = await http.get("/console")
        rejected = await http.get("/v1/console/status")
        accepted = await http.get(
            "/v1/console/status",
            headers={"X-Knoa-Console": adapter._console_csrf_token},
        )
        diagnostics = await http.get(
            "/v1/console/diagnostics",
            headers={"X-Knoa-Console": adapter._console_csrf_token},
        )
        unconfirmed_repair = await http.post(
            "/v1/console/diagnostics/repair",
            headers={"X-Knoa-Console": adapter._console_csrf_token},
            json={"repair_action_id": "restart_node", "confirmed": False},
        )
        invalid = await http.post(
            "/v1/console/hub/enroll",
            headers={"X-Knoa-Console": adapter._console_csrf_token},
            json={"version": "wrong"},
        )
        pairing = await http.post(
            "/v1/console/pairing",
            headers={"X-Knoa-Console": adapter._console_csrf_token},
            json={},
        )
        lifecycle = await http.get(
            "/v1/console/lifecycle",
            headers={"X-Knoa-Console": adapter._console_csrf_token},
        )
        configuration = await http.get(
            "/v1/console/config",
            headers={"X-Knoa-Console": adapter._console_csrf_token},
        )
        extensions = await http.get(
            "/v1/console/extensions",
            headers={"X-Knoa-Console": adapter._console_csrf_token},
        )
        invalid_document = copy.deepcopy(
            configuration.json()["revision"]["document"]
        )
        invalid_document["providers"]["bootstrap_provider"]["driver"] = "invalid"
        invalid_configuration = await http.post(
            "/v1/console/config/publish",
            headers={"X-Knoa-Console": adapter._console_csrf_token},
            json={"document": invalid_document, "summary": "Invalid Console test"},
        )
        published = await http.post(
            "/v1/console/config/publish",
            headers={"X-Knoa-Console": adapter._console_csrf_token},
            json={
                "document": configuration.json()["revision"]["document"],
                "summary": "Console test",
            },
        )
        secret = await http.put(
            "/v1/console/secrets/provider-test",
            headers={"X-Knoa-Console": adapter._console_csrf_token},
            json={"value": "secret-value"},
        )
    async with httpx.AsyncClient(transport=remote, base_url="http://node") as http:
        hidden = await http.get("/console")

    assert page.status_code == 200
    assert "Knoa Node Console" in page.text
    assert adapter._console_csrf_token in page.text
    assert rejected.status_code == 403
    assert accepted.status_code == 200
    assert accepted.json()["hub"]["enrolled"] is False
    assert accepted.json()["p2p"]["available"] is True
    assert accepted.json()["p2p"]["offers_total"] == 0
    assert diagnostics.status_code == 200
    assert diagnostics.json()["status"] in {"warning", "error"}
    assert {item["id"] for item in diagnostics.json()["checks"]} >= {
        "node", "mdns", "app_lan_discovery", "p2p", "relay", "config", "codex", "vision",
    }
    assert accepted.json()["runtime_version"]
    assert extensions.status_code == 200
    assert {"catalog", "installations", "skills", "mcp_servers"} <= set(
        extensions.json()
    )
    assert any(
        item["id"] == "knoa.browser" for item in extensions.json()["catalog"]
    )
    assert accepted.json()["versions"]["runtime_platform_version"] == accepted.json()["runtime_version"]
    assert accepted.json()["versions"]["config_revision"] == "revision-a"
    assert isinstance(accepted.json()["versions"]["component_generations"], list)
    relay_check = next(
        item for item in diagnostics.json()["checks"] if item["id"] == "relay"
    )
    assert relay_check["repair_action_id"] == "retry_relay"
    assert relay_check["repair"]["effect"] == "network_write"
    assert unconfirmed_repair.status_code == 409
    assert unconfirmed_repair.json()["error"] == "confirmation_required"
    assert invalid.status_code == 400
    assert invalid.json() == {"error": "invalid_enrollment_code"}
    assert pairing.status_code == 409
    assert pairing.json() == {"error": "node_not_enrolled"}
    assert lifecycle.status_code == 503
    assert lifecycle.json() == {"error": "lifecycle_not_installed"}
    assert configuration.status_code == 200
    assert configuration.json()["revision"]["document"]["agents"]["default_agent"] == "knoa"
    assert invalid_configuration.status_code == 400
    assert invalid_configuration.json()["error"] == "invalid_configuration"
    assert "Input should be" in invalid_configuration.json()["detail"]
    assert published.status_code == 200
    assert published.json()["result"]["revision"]["revision_id"] == "revision-b"
    assert secret.status_code == 200
    assert secret.json()["configured"] is True
    assert hidden.status_code == 404


def _task_snapshot() -> TaskSnapshot:
    return TaskSnapshot(
        task_id="task-a",
        session_handle="session-a",
        agent_id="knoa",
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


def _product_task_snapshot(
    state: TaskDefinitionState = TaskDefinitionState.ACTIVE,
) -> ProductTaskSnapshot:
    return ProductTaskSnapshot(
        task_id="task-a",
        session_handle="session-a",
        agent_id="knoa",
        title="hello",
        goal="hello",
        tools_enabled=True,
        priority=0,
        launch_policy=TaskLaunchPolicy(),
        notification_policy={"completed": True},
        state=state,
        revision=1,
        latest_execution_id="execution-a",
        execution_count=1,
        latest_execution_state=TaskState.RUNNING,
        latest_execution_phase="working",
        latest_execution_summary="Checking failed jobs",
        latest_execution_updated_at=2.0,
        pending_approval_count=1,
        created_at=1.0,
        updated_at=2.0,
    )


def _product_execution_snapshot(
    state: TaskState = TaskState.RUNNING,
) -> ProductTaskExecutionSnapshot:
    return ProductTaskExecutionSnapshot(
        execution_id="execution-a",
        task_id="task-a",
        agent_id_snapshot="knoa",
        task_revision=1,
        launch_reason=TaskLaunchReason.CREATED,
        goal_snapshot="hello",
        policy_snapshot=TaskLaunchPolicy(),
        state=state,
        phase="working",
        created_at=1.0,
        updated_at=2.0,
    )


def _chat_snapshot(state: ChatTurnState = ChatTurnState.COMPLETED) -> ChatTurnSnapshot:
    return ChatTurnSnapshot(
        turn_id="turn-a",
        session_handle="session-a",
        client_request_id="request-a",
        user_input="hello",
        tools_enabled=True,
        state=state,
        content="你好",
        final_output="你好",
        cancel_requested=False,
        created_at=1.0,
        updated_at=2.0,
        finished_at=2.0,
        revision=2,
    )


class _Core:
    def __init__(self, config: AppConfig | None = None) -> None:
        self.calls = []
        self.closed = False
        self.config = config or AppConfig()
        # Mirrors the real Core: executions appear only after an explicit
        # execute (the Gateway's create-and-run no longer auto-launches).
        self.launched_executions = []

    async def close(self):
        self.closed = True

    async def get_config_current(self, principal_id):
        self.calls.append(("get_config_current", principal_id))
        document = self.config.managed_config()
        return (
            ConfigRevision(
                revision_id="revision-a",
                document=document,
                config_digest=document.digest,
                created_by=principal_id,
                created_at=1.0,
            ),
            ConfigControlState(
                desired_revision_id="revision-a",
                applied_revision_id="revision-a",
                updated_at=1.0,
            ),
            (),
        )

    async def create_config_draft(self, principal_id):
        self.calls.append(("create_config_draft", principal_id))
        return SimpleNamespace(draft_id="draft-a", draft_version=1)

    async def replace_config_draft(
        self, principal_id, draft_id, document, *, expected_version
    ):
        self.calls.append(
            ("replace_config_draft", principal_id, draft_id, expected_version)
        )
        return SimpleNamespace(
            draft_id=draft_id,
            draft_version=2,
            document=document,
        )

    async def validate_config_draft(self, principal_id, draft_id, *, preflight):
        self.calls.append(("validate_config_draft", principal_id, draft_id, preflight))
        return SimpleNamespace(valid=True, model_dump=lambda **_: {"valid": True, "issues": []})

    async def publish_config_draft(
        self, principal_id, draft_id, *, expected_version, summary
    ):
        self.calls.append(
            ("publish_config_draft", principal_id, draft_id, expected_version, summary)
        )
        return SimpleNamespace(
            model_dump=lambda **_: {
                "revision": {"revision_id": "revision-b"},
                "state": {"apply_status": "applying"},
            }
        )

    async def create_session(self, principal_id, **kwargs):
        self.calls.append(("create_session", principal_id, kwargs))
        return "session-a"

    async def create_task(self, principal_id, session_handle, user_input, attachments, **kwargs):
        self.calls.append(
            ("create_task", principal_id, session_handle, user_input, attachments, kwargs)
        )
        return SimpleNamespace(task_id="task-a", state=TaskState.QUEUED)

    async def create_product_task(
        self, principal_id, session_handle, goal, *, title, attachments,
        client_request_id, tools_enabled, priority, launch_policy, notification_policy,
        agent_id=None, auto_launch=True,
    ):
        self.calls.append((
            "create_product_task", principal_id, session_handle, goal, title,
            client_request_id, attachments, tools_enabled, priority, launch_policy,
            notification_policy, agent_id, auto_launch,
        ))
        return SimpleNamespace(
            task=_product_task_snapshot(),
            execution=_product_execution_snapshot() if auto_launch else None,
        )

    async def get_product_task(self, principal_id, task_id):
        self.calls.append(("get_product_task", principal_id, task_id))
        return _product_task_snapshot()

    async def preflight_product_task(self, principal_id, task_id):
        self.calls.append(("preflight_product_task", principal_id, task_id))
        task = await self.get_product_task(principal_id, task_id)
        runtime = await self.status(principal_id, task.session_handle)
        revision, _control, _generations = await self.get_config_current(principal_id)
        checks = [
            TaskPreflightCheck(
                check_id="task_state",
                status=(
                    "ready"
                    if task.state is TaskDefinitionState.ACTIVE
                    else "blocked"
                ),
                detail=(
                    "任务可以执行"
                    if task.state is TaskDefinitionState.ACTIVE
                    else "任务当前未启用，请先恢复任务"
                ),
                recommended_action=(
                    "none"
                    if task.state is TaskDefinitionState.ACTIVE
                    else "resume"
                ),
            ),
            TaskPreflightCheck(
                check_id="goal",
                status="ready",
                detail="执行目标已设置",
            ),
            TaskPreflightCheck(
                check_id="agent_config",
                status="ready",
                detail="任务使用的 Agent 已配置",
            ),
            TaskPreflightCheck(
                check_id="config",
                status="ready",
                detail="Node 配置已应用",
            ),
            TaskPreflightCheck(
                check_id="runtime",
                status="ready" if runtime.connected else "blocked",
                detail=(
                    "Agent Runtime 可用"
                    if runtime.connected
                    else "Agent Runtime 当前不可用，请检查 Node 状态后重试"
                ),
                recommended_action="none" if runtime.connected else "retry",
            ),
        ]
        agent = revision.document.agents.agents.get(task.agent_id)
        if agent is not None and agent.kind == "codex":
            executable = agent.command[0] if agent.command else ""
            ready = bool(executable and shutil.which(executable))
            checks.append(TaskPreflightCheck(
                check_id="runtime_binary",
                status="ready" if ready else "blocked",
                detail=(
                    "Codex Runtime 命令可用"
                    if ready
                    else "找不到 Codex Runtime 命令，请在 Node 上安装或修正 Agent 配置"
                ),
                recommended_action="none" if ready else "configure",
            ))
        result_checks = tuple(checks)
        return TaskPreflightResult(
            task_id=task.task_id,
            ready=not any(check.status == "blocked" for check in result_checks),
            checks=result_checks,
        )

    async def list_product_tasks(self, principal_id, **kwargs):
        self.calls.append(("list_product_tasks", principal_id, kwargs))
        return (_product_task_snapshot(),)

    async def update_product_task(self, principal_id, task_id, **changes):
        self.calls.append(("update_product_task", principal_id, task_id, changes))
        return _product_task_snapshot()

    async def set_product_task_state(self, principal_id, task_id, state):
        self.calls.append(("set_product_task_state", principal_id, task_id, state))
        return _product_task_snapshot(state)

    async def delete_product_task(self, principal_id, task_id):
        self.calls.append(("delete_product_task", principal_id, task_id))

    async def execute_product_task(self, principal_id, task_id, *, launch_reason="manual"):
        self.calls.append(("execute_product_task", principal_id, task_id, launch_reason))
        self.launched_executions.append(launch_reason)
        return _product_execution_snapshot()

    async def list_product_task_executions(self, principal_id, task_id, *, limit):
        self.calls.append(("list_product_task_executions", principal_id, task_id, limit))
        return tuple(_product_execution_snapshot() for _ in self.launched_executions)

    async def get_product_task_execution(self, principal_id, execution_id):
        self.calls.append(("get_product_task_execution", principal_id, execution_id))
        return _product_execution_snapshot()

    async def delete_product_task_execution(self, principal_id, execution_id):
        self.calls.append(("delete_product_task_execution", principal_id, execution_id))

    async def rerun_product_task_execution(self, principal_id, execution_id):
        self.calls.append(("rerun_product_task_execution", principal_id, execution_id))
        return _product_execution_snapshot(TaskState.QUEUED)

    async def create_chat_turn(self, principal_id, session_handle, user_input, attachments, **kwargs):
        self.calls.append(
            ("create_chat_turn", principal_id, session_handle, user_input, attachments, kwargs)
        )
        return _chat_snapshot(ChatTurnState.RUNNING)

    async def get_chat_turn(self, principal_id, turn_id):
        self.calls.append(("get_chat_turn", principal_id, turn_id))
        return _chat_snapshot()

    async def list_chat_turns(self, principal_id, session_handle, *, limit, cursor=""):
        self.calls.append(("list_chat_turns", principal_id, session_handle, limit, cursor))
        return (_chat_snapshot(),), "next-turn"

    async def chat_turn_updates(self, principal_id, turn_id):
        self.calls.append(("chat_turn_updates", principal_id, turn_id))
        yield _chat_snapshot()

    async def cancel_chat_turn(self, principal_id, turn_id):
        self.calls.append(("cancel_chat_turn", principal_id, turn_id))
        return _chat_snapshot(ChatTurnState.CANCELLED)

    async def resolve_chat_approval(self, principal_id, approval_id, *, approved):
        self.calls.append(("resolve_chat_approval", principal_id, approval_id, approved))
        return SimpleNamespace(
            approval=ChatApprovalSnapshot(
                approval_id=approval_id,
                step_id="step-a",
                tool_call_id="call-a",
                tool_name="write_file",
                state="approved" if approved else "rejected",
                created_at=1.0,
            ),
            resolved=True,
        )

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
async def test_gateway_adapter_lists_only_enabled_agents(tmp_path) -> None:
    base = _config(tmp_path)
    agents = {
        **base.node_agents,
        "knoa": base.node_agents["knoa"].model_copy(
            update={
                "delegation": base.node_agents["knoa"].delegation.model_copy(
                    update={"targets": frozenset()}
                )
            }
        ),
        "codex": base.node_agents["codex"].model_copy(
            update={
                "enabled": True,
                "visibility": "user",
                "command": ("knoa-test-codex-not-installed", "app-server"),
            }
        ),
    }
    config = AppConfig(**{
        **base.model_dump(),
        "node_agents": agents,
    })
    adapter = SecureGatewayAdapter(
        config,
        authentication=_Authentication(),
        core=_Core(config),
    )
    transport = httpx.ASGITransport(app=adapter.app)
    headers = {"Authorization": "Bearer " + "v1.gws-a." + "t" * 43}

    async with httpx.AsyncClient(transport=transport, base_url="http://gateway.local") as http:
        response = await http.get("/v1/agents", headers=headers)

    assert response.status_code == 200
    assert response.json() == {
        "default_agent": "knoa",
        "agents": [
            {"agent_id": "knoa", "display_name": "Knoa Agent"},
        ],
    }

    async with httpx.AsyncClient(transport=transport, base_url="http://gateway.local") as http:
        availability = await http.get("/v1/agents/availability", headers=headers)
    assert availability.status_code == 200
    codex = next(item for item in availability.json()["unavailable"] if item["agent_id"] == "codex")
    assert codex["reason"] == "runtime_unavailable"


@pytest.mark.asyncio
async def test_gateway_adapter_exposes_owner_configuration_current(tmp_path) -> None:
    config = _config(tmp_path)
    core = _Core(config)
    adapter = SecureGatewayAdapter(
        config,
        authentication=_Authentication(),
        core=core,
    )
    transport = httpx.ASGITransport(app=adapter.app)
    headers = {"Authorization": "Bearer " + "v1.gws-a." + "t" * 43}

    async with httpx.AsyncClient(transport=transport, base_url="http://gateway.local") as http:
        response = await http.get("/v1/config/current", headers=headers)

    assert response.status_code == 200
    assert response.json()["state"]["applied_revision_id"] == "revision-a"
    assert response.json()["revision"]["document"]["agents"]["default_agent"] == "knoa"


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
            json={"client_request_id": "task-request-a", "goal": "hello"},
        )
        listed = await http.get(
            "/v1/tasks?state=active&limit=10",
            headers=headers,
        )
        detail = await http.get("/v1/tasks/task-a", headers=headers)
        preflight = await http.get(
            "/v1/tasks/task-a/preflight",
            headers=headers,
        )
        timeline = await http.get(
            "/v1/task-executions/execution-a/events?after_seq=2",
            headers=headers,
        )
        cancelled = await http.post(
            "/v1/task-executions/execution-a/cancel",
            headers=headers,
            json={"reason": "owner request"},
        )
        paused = await http.post(
            "/v1/task-executions/execution-a/pause",
            headers=headers,
            json={"reason": "later"},
        )
        resumed = await http.post(
            "/v1/task-executions/execution-a/resume",
            headers=headers,
            json={"reason": "continue", "acknowledge_outcome_unknown": True},
        )
        retried = await http.post(
            "/v1/task-executions/execution-a/rerun",
            headers=headers,
        )
        approval = await http.post(
            "/v1/approvals/approval-a/resolve",
            headers=headers,
            json={"approved": True},
        )

    assert session.status_code == 201
    assert session.json() == {"session_handle": "session-a"}
    assert created.status_code == 201
    assert created.json()["task"]["task_id"] == "task-a"
    assert created.json()["execution"]["execution_id"] == "execution-a"
    assert listed.json()["tasks"][0]["task_id"] == "task-a"
    assert listed.json()["tasks"][0]["execution_count"] == 1
    assert listed.json()["tasks"][0]["latest_execution_state"] == "running"
    assert listed.json()["tasks"][0]["pending_approval_count"] == 1
    assert detail.json()["task"]["state"] == "active"
    assert preflight.status_code == 200
    assert preflight.json()["ready"] is True
    assert {item["status"] for item in preflight.json()["checks"]} == {"ready"}
    assert timeline.json()["events"][0]["event_seq"] == 3
    assert timeline.json()["events"][0]["payload"]["content"] == "分析中"
    assert cancelled.json() == {"accepted": True, "state": "cancelled"}
    assert paused.json() == {"accepted": True, "state": "paused"}
    assert resumed.json() == {"accepted": True, "state": "queued"}
    assert retried.status_code == 202
    assert retried.json()["execution"]["task_id"] == "task-a"
    assert retried.json()["execution"]["state"] == "queued"
    assert approval.json() == {
        "approval_id": "approval-a",
        "resolved": True,
        "state": "approved",
    }
    assert {call[1] for call in core.calls} == {"personal:owner"}


@pytest.mark.asyncio
async def test_gateway_task_preflight_blocks_paused_task_and_disconnected_runtime(tmp_path) -> None:
    core = _Core()

    async def paused_task(principal_id, task_id):
        return _product_task_snapshot(TaskDefinitionState.PAUSED)

    async def disconnected_status(principal_id, session_handle):
        return RuntimeStatus(status="disconnected", connected=False)

    core.get_product_task = paused_task
    core.status = disconnected_status
    adapter = SecureGatewayAdapter(
        _config(tmp_path),
        authentication=_Authentication(),
        core=core,
    )
    transport = httpx.ASGITransport(app=adapter.app)
    headers = {"Authorization": "Bearer " + "v1.gws-a." + "t" * 43}
    async with httpx.AsyncClient(transport=transport, base_url="http://gateway.local") as http:
        response = await http.get("/v1/tasks/task-a/preflight", headers=headers)

    body = response.json()
    assert response.status_code == 200
    assert body["ready"] is False
    assert {item["check_id"] for item in body["checks"] if item["status"] == "blocked"} == {
        "task_state",
        "runtime",
    }


@pytest.mark.asyncio
async def test_gateway_execute_enforces_preflight_before_core_command(tmp_path) -> None:
    core = _Core()

    async def paused_task(principal_id, task_id):
        return _product_task_snapshot(TaskDefinitionState.PAUSED)

    core.get_product_task = paused_task
    adapter = SecureGatewayAdapter(
        _config(tmp_path),
        authentication=_Authentication(),
        core=core,
    )
    transport = httpx.ASGITransport(app=adapter.app)
    headers = {"Authorization": "Bearer " + "v1.gws-a." + "t" * 43}
    async with httpx.AsyncClient(transport=transport, base_url="http://gateway.local") as http:
        response = await http.post("/v1/tasks/task-a/execute", headers=headers)

    assert response.status_code == 409
    assert response.json()["error"] == "preflight_blocked"
    assert not any(call[0] == "execute_product_task" for call in core.calls)


@pytest.mark.asyncio
async def test_gateway_preflight_reports_missing_codex_runtime_binary(tmp_path) -> None:
    core = _Core()
    document = core.config.managed_config()
    codex = document.agents.agents["codex"].model_copy(
        update={"enabled": True, "command": ("knoa-test-codex-not-installed", "app-server")}
    )
    agents = document.agents.model_copy(
        update={"agents": {**document.agents.agents, "codex": codex}}
    )
    document = document.model_copy(update={"agents": agents})

    async def codex_task(principal_id, task_id):
        return _product_task_snapshot().model_copy(update={"agent_id": "codex"})

    async def current_config(principal_id):
        return (
            ConfigRevision(
                revision_id="revision-codex",
                document=document,
                config_digest=document.digest,
                created_by=principal_id,
                created_at=1.0,
            ),
            ConfigControlState(
                desired_revision_id="revision-codex",
                applied_revision_id="revision-codex",
                updated_at=1.0,
            ),
            (),
        )

    core.get_product_task = codex_task
    core.get_config_current = current_config
    adapter = SecureGatewayAdapter(
        _config(tmp_path),
        authentication=_Authentication(),
        core=core,
    )
    transport = httpx.ASGITransport(app=adapter.app)
    headers = {"Authorization": "Bearer " + "v1.gws-a." + "t" * 43}
    async with httpx.AsyncClient(transport=transport, base_url="http://gateway.local") as http:
        response = await http.get("/v1/tasks/task-a/preflight", headers=headers)

    body = response.json()
    assert response.status_code == 200
    assert body["ready"] is False
    assert next(item for item in body["checks"] if item["check_id"] == "runtime_binary") == {
        "check_id": "runtime_binary",
        "status": "blocked",
        "detail": "找不到 Codex Runtime 命令，请在 Node 上安装或修正 Agent 配置",
        "recommended_action": "configure",
    }


@pytest.mark.asyncio
async def test_gateway_conversation_uses_turn_snapshots_not_task_feed(tmp_path) -> None:
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
        created = await http.post(
            "/v1/conversations/sessions/session-a/turns",
            headers=headers,
            json={"client_request_id": "chat-request-a", "input": "hello"},
        )
        listed = await http.get(
            "/v1/conversations/sessions/session-a/turns?limit=20",
            headers=headers,
        )
        detail = await http.get(
            "/v1/conversations/turns/turn-a",
            headers=headers,
        )
        stream = await http.get(
            "/v1/conversations/turns/turn-a/stream",
            headers=headers,
        )
        cancelled = await http.post(
            "/v1/conversations/turns/turn-a/cancel",
            headers=headers,
        )
        approval = await http.post(
            "/v1/conversations/approvals/approval-a/resolve",
            headers=headers,
            json={"approved": True},
        )

    assert created.status_code == 202
    assert created.json()["turn"]["turn_id"] == "turn-a"
    assert listed.json()["turns"][0]["final_output"] == "你好"
    assert listed.json()["next_cursor"] == "next-turn"
    create_call = next(call for call in core.calls if call[0] == "create_chat_turn")
    assert create_call[-1]["client_request_id"] == "chat-request-a"
    assert detail.json()["turn"]["state"] == "completed"
    assert "event: snapshot\n" in stream.text
    assert '"turn_id":"turn-a"' in stream.text
    assert cancelled.json()["turn"]["state"] == "cancelled"
    assert approval.json()["approval"]["state"] == "approved"
    assert not any(call[0] == "principal_task_events" for call in core.calls)


@pytest.mark.asyncio
async def test_gateway_creates_background_task_in_detached_session(tmp_path) -> None:
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
        response = await http.post(
            "/v1/tasks",
            headers=headers,
            json={
                "client_request_id": "task-request-a",
                "goal": "整理资料",
            },
        )

    assert response.status_code == 201
    assert core.calls[0] == (
        "create_session",
        "personal:owner",
        {"activate": False, "agent_id": None},
    )
    create = core.calls[1]
    assert create[0:4] == (
        "create_product_task",
        "personal:owner",
        "session-a",
        "整理资料",
    )
    assert create[5] == "task-request-a"


@pytest.mark.asyncio
async def test_gateway_selects_explicit_agent_for_background_task(tmp_path) -> None:
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
        response = await http.post(
            "/v1/tasks",
            headers=headers,
            json={
                "client_request_id": "task-request-codex",
                "goal": "分析问题",
                "agent_id": "codex",
            },
        )

    assert response.status_code == 201
    assert core.calls[0] == (
        "create_session",
        "personal:owner",
        {"activate": False, "agent_id": "codex"},
    )
    create = core.calls[1]
    assert create[-2] == "codex"
    assert create[-1] is False  # create-and-run defers launch for preflight
    assert "execute_product_task" in {call[0] for call in core.calls}
    assert ("execute_product_task", "personal:owner", "task-a", "created") in core.calls


@pytest.mark.asyncio
async def test_gateway_create_and_run_blocked_by_preflight_keeps_definition(tmp_path) -> None:
    class _ArchivedTaskCore(_Core):
        async def get_product_task(self, principal_id, task_id):
            return _product_task_snapshot(TaskDefinitionState.ARCHIVED)

    core = _ArchivedTaskCore()
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
        response = await http.post(
            "/v1/tasks",
            headers=headers,
            json={
                "client_request_id": "task-request-blocked",
                "goal": "整理资料",
            },
        )

    assert response.status_code == 409
    body = response.json()
    assert body["error"] == "preflight_blocked"
    assert body["task"]["task_id"] == "task-a"
    assert any(
        check["status"] == "blocked" for check in body["preflight"]["checks"]
    )
    # The definition survives so the user can fix the environment and start
    # the task from its page; nothing was executed.
    assert core.launched_executions == []


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
async def test_gateway_adapter_revokes_current_device(tmp_path) -> None:
    adapter = SecureGatewayAdapter(
        _config(tmp_path),
        authentication=_Authentication(),
        core=_Core(),
    )
    transport = httpx.ASGITransport(app=adapter.app)
    headers = {"Authorization": "Bearer " + "v1.gws-a." + "t" * 43}
    async with httpx.AsyncClient(transport=transport, base_url="http://gateway.local") as http:
        response = await http.delete("/v1/device", headers=headers)

    assert response.status_code == 200
    assert response.json() == {"revoked": True}


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
        response = await http.get("/v1/events?after_id=40", headers={**headers, "Last-Event-ID": "41"})

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert "id: 42\n" in response.text
    assert "event: content_delta\n" in response.text
    assert '"content":"你好"' in response.text
    assert core.calls == [("principal_task_events", "personal:owner", 41)]


@pytest.mark.asyncio
async def test_gateway_adapter_polls_a_finite_event_page_for_relay(tmp_path) -> None:
    core = _Core()
    adapter = SecureGatewayAdapter(
        _config(tmp_path),
        authentication=_Authentication(),
        core=core,
    )
    transport = httpx.ASGITransport(app=adapter.app)
    headers = {"Authorization": "Bearer " + "v1.gws-a." + "t" * 43}
    async with httpx.AsyncClient(transport=transport, base_url="http://gateway.local") as http:
        response = await http.get(
            "/v1/events/poll?after_id=41&limit=10",
            headers=headers,
        )

    assert response.status_code == 200
    assert response.json()["events"][0]["feed_event_id"] == 42
    assert core.calls == [("principal_task_events", "personal:owner", 41)]


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
        assert response.json()["status"] == "ok"
        assert response.json()["scope"] == "authentication"
        assert response.json()["node_id"].startswith("node_")
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
        assert response.json()["status"] == "ok"
        assert response.json()["scope"] == "authentication"
        assert response.json()["node_id"].startswith("node_")
    finally:
        await adapter.stop()


def test_gateway_adapter_requires_owner_only_tls_private_key(tmp_path) -> None:
    config = _tls_config(tmp_path)
    key_path = tmp_path / "gateway-key.pem"
    key_path.chmod(0o640)

    with pytest.raises(ValueError, match="owner-only"):
        SecureGatewayAdapter(config, authentication=_Authentication())
