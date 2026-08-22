from __future__ import annotations

import asyncio
import secrets
import time
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from knoa_platform.agent_runtime.model_step import ProviderCallRequest, ProviderChunk
from knoa_platform.config import AppConfig, ResolvedModelConfig
from knoa_platform.configuration.models import ManagedModelDeploymentConfig
from knoa_platform.hub.repository import HubRepository
from knoa_platform.hub.service import HubService
from knoa_platform.node_hub import NodeHubStore
from knoa_platform.node_identity import NodeIdentity, NodeIdentityStore
from knoa_platform.relay_protocol import canonical_json
from knoa_platform.remote_models import (
    RemoteModelEndpoint,
    RemoteModelInvocationRepository,
    RemoteModelProvider,
    materialized_deployment_digest,
)
from knoa_platform.resource_protocol import (
    accept_resource_client_hello,
    create_resource_client_hello,
    finish_resource_client_handshake,
    verify_resource_ticket,
)
from knoa_platform.runtime import RuntimePaths


def _enroll(
    repository: HubRepository,
    hub: HubService,
    identity: NodeIdentity,
    name: str,
) -> None:
    grant = repository.create_enrollment_grant()
    transcript = {
        "audience": "knoa-node-enrollment-v1",
        "hub_id": hub.hub_id,
        "grant_id": grant.grant_id,
        "challenge": grant.challenge,
        "node_id": identity.node_id,
        "signing_public_key": identity.signing_public_key,
        "signing_key_version": identity.signing_key_version,
        "configuration_public_key": identity.configuration_public_key,
        "configuration_key_version": identity.configuration_key_version,
    }
    hub.enroll_node(
        {
            **transcript,
            "grant_secret": grant.secret,
            "display_name": name,
            "platform": "linux",
            "version": "1",
            "signature": identity.sign(canonical_json(transcript)),
        }
    )


def _resource_ticket(
    tmp_path: Path,
    *,
    hub_id: str = "workspace-1",
    workspace_id: str = "workspace-1",
) -> tuple[HubService, NodeIdentity, NodeIdentity, str]:
    now = time.time()
    repository = HubRepository(tmp_path / "hub.db", hub_id=workspace_id)
    hub = HubService(
        repository,
        tmp_path / "hub.key",
        owner_token="o" * 43,
        hub_id=hub_id,
    )
    target = NodeIdentityStore(tmp_path / "target.json").load_or_create()
    caller = NodeIdentityStore(tmp_path / "caller.json").load_or_create()
    _enroll(repository, hub, target, "Target")
    _enroll(repository, hub, caller, "Caller")
    repository.record_presence(
        target.node_id,
        "target-presence-nonce",
        direct_gateway_url="https://node-target.example.test",
    )
    repository.put_workspace_resource(
        {
            "resource_id": "personal_model",
            "kind": "model",
            "generation": 1,
            "canonical_digest": "a" * 64,
            "display_name": "Qwen 3.5 4B",
            "spec": {
                "provider_protocol": "openai_compatible",
                "model_identity": "qwen3.5-4b",
                "declared_capabilities": {"streaming": True},
            },
            "enabled": True,
        },
        created_by="subject_owner",
    )
    repository.put_deployment(
        {
            "deployment_id": "qwen_local",
            "kind": "model",
            "resource_id": "personal_model",
            "resource_generation": 1,
            "resource_digest": "a" * 64,
            "target_node_id": target.node_id,
            "desired_generation": 1,
            "spec": {"max_remote_concurrency": 1},
            "enabled": True,
        }
    )
    repository.put_resource_grant(
        {
            "grant_id": "grant_caller_qwen",
            "caller_node_id": caller.node_id,
            "target_deployment_id": "qwen_local",
            "max_request_deadline": 300,
            "expires_at": now + 3600,
        }
    )
    observation = {
        "node_id": target.node_id,
        "deployment_id": "qwen_local",
        "applied_digest": "b" * 64,
        "health_epoch": 1,
        "health": "healthy",
        "capabilities": {"streaming": True},
        "available_capacity": 1,
        "observed_at": now,
        "expires_at": now + 90,
    }
    observation_transcript = {
        "audience": "knoa-deployment-observation-v1",
        "workspace_id": workspace_id,
        **observation,
    }
    hub.publish_deployment_observation(
        {
            **observation,
            "signature": target.sign(canonical_json(observation_transcript)),
        }
    )
    request = {
        "invocation_id": "invocation-1",
        "caller_node_id": caller.node_id,
        "target_deployment_id": "qwen_local",
        "max_deadline": 600,
        "timestamp": now,
        "nonce": "n" * 24,
    }
    request_transcript = {
        "audience": "knoa-resource-ticket-request-v1",
        "workspace_id": workspace_id,
        **request,
    }
    ticket = hub.issue_resource_ticket(
        {
            **request,
            "signature": caller.sign(canonical_json(request_transcript)),
        }
    )
    return hub, target, caller, ticket


def _node_control_request(
    identity: NodeIdentity,
    *,
    workspace_id: str,
    audience: str,
    values: dict,
    nonce: str | None = None,
) -> dict:
    payload = {
        "node_id": identity.node_id,
        **values,
        "timestamp": time.time(),
        "nonce": nonce or secrets.token_urlsafe(24),
    }
    transcript = {"audience": audience, "workspace_id": workspace_id, **payload}
    return {**payload, "signature": identity.sign(canonical_json(transcript))}


def test_node_owned_model_share_grants_are_scoped_replay_safe_and_revocable(
    tmp_path: Path,
) -> None:
    repository = HubRepository(tmp_path / "hub.db", hub_id="workspace-1")
    hub = HubService(repository, tmp_path / "hub.key", owner_token="o" * 43)
    target = NodeIdentityStore(tmp_path / "target.json").load_or_create()
    caller = NodeIdentityStore(tmp_path / "caller.json").load_or_create()
    attacker = NodeIdentityStore(tmp_path / "attacker.json").load_or_create()
    for identity, name in ((target, "Target"), (caller, "Caller"), (attacker, "Attacker")):
        _enroll(repository, hub, identity, name)
    values = {
        "deployment_id": "shared-qwen",
        "resource_id": "qwen-resource",
        "display_name": "Qwen 3.5 4B",
        "model_identity": "qwen3.5-4b",
        "provider_protocol": "openai_compatible",
        "supports_vision": True,
        "materialized_digest": "a" * 64,
        "max_remote_concurrency": 1,
        "allowed_node_ids": [caller.node_id],
        "enabled": True,
    }
    request = _node_control_request(
        target,
        workspace_id="workspace-1",
        audience="knoa-node-model-share-v1",
        values=values,
        nonce="model-share-once-1234567890",
    )

    published = hub.publish_node_model_share(request)

    assert published["deployment"]["target_node_id"] == target.node_id
    assert published["allowed_node_ids"] == [caller.node_id]
    caller_state = hub.node_control_state(_node_control_request(
        caller,
        workspace_id="workspace-1",
        audience="knoa-node-control-state-v1",
        values={},
    ))
    assert [item["deployment_id"] for item in caller_state["deployments"]] == ["shared-qwen"]
    assert "api_key" not in str(caller_state).lower()
    with pytest.raises(PermissionError, match="nonce"):
        hub.publish_node_model_share(request)

    with pytest.raises(PermissionError, match="Workspace Resource"):
        hub.publish_node_model_share(_node_control_request(
            attacker,
            workspace_id="workspace-1",
            audience="knoa-node-model-share-v1",
            values={**values, "deployment_id": "attacker-deployment", "allowed_node_ids": []},
        ))

    disabled = hub.publish_node_model_share(_node_control_request(
        target,
        workspace_id="workspace-1",
        audience="knoa-node-model-share-v1",
        values={**values, "allowed_node_ids": [], "enabled": False},
    ))
    assert disabled["deployment"]["enabled"] is False
    with pytest.raises(PermissionError):
        repository.active_resource_grant(caller.node_id, "shared-qwen")


def test_node_can_publish_matching_workspace_owned_model_deployment(
    tmp_path: Path,
) -> None:
    repository = HubRepository(tmp_path / "hub.db", hub_id="workspace-1")
    hub = HubService(repository, tmp_path / "hub.key", owner_token="o" * 43)
    target = NodeIdentityStore(tmp_path / "target.json").load_or_create()
    caller = NodeIdentityStore(tmp_path / "caller.json").load_or_create()
    _enroll(repository, hub, target, "Target")
    _enroll(repository, hub, caller, "Caller")
    spec = {
        "provider_protocol": "openai_compatible",
        "model_identity": "qwen3.5-4b",
        "declared_capabilities": {
            "streaming": True,
            "tools": True,
            "vision": True,
        },
    }
    digest = "b" * 64
    resource = repository.put_workspace_resource(
        {
            "resource_id": "workspace-qwen",
            "kind": "model",
            "generation": 3,
            "canonical_digest": digest,
            "display_name": "Workspace Qwen",
            "spec": spec,
            "enabled": True,
        },
        created_by="workspace-owner",
    )
    repository.put_deployment(
        {
            "deployment_id": "workspace-qwen-on-target",
            "kind": "model",
            "resource_id": resource["resource_id"],
            "resource_generation": resource["generation"],
            "resource_digest": resource["canonical_digest"],
            "target_node_id": target.node_id,
            "desired_generation": 1,
            "spec": {},
            "enabled": True,
        }
    )

    published = hub.publish_node_model_share(
        _node_control_request(
            target,
            workspace_id="workspace-1",
            audience="knoa-node-model-share-v1",
            values={
                "deployment_id": "workspace-qwen-on-target",
                "resource_id": "workspace-qwen",
                "display_name": "Workspace Qwen",
                "model_identity": "qwen3.5-4b",
                "provider_protocol": "openai_compatible",
                "supports_vision": True,
                "materialized_digest": "a" * 64,
                "max_remote_concurrency": 2,
                "allowed_node_ids": [caller.node_id],
                "enabled": True,
            },
        )
    )

    assert published["resource"]["created_by"] == "workspace-owner"
    assert published["resource"]["generation"] == 3
    assert published["deployment"]["target_node_id"] == target.node_id
    assert published["deployment"]["resource_digest"] == digest
    assert published["allowed_node_ids"] == [caller.node_id]


def test_workspace_resources_share_one_deployment_envelope_and_mcp_grant(
    tmp_path: Path,
) -> None:
    now = time.time()
    repository = HubRepository(tmp_path / "hub.db", hub_id="workspace-1")
    hub = HubService(repository, tmp_path / "hub.key", owner_token="o" * 43)
    target = NodeIdentityStore(tmp_path / "target.json").load_or_create()
    caller = NodeIdentityStore(tmp_path / "caller.json").load_or_create()
    _enroll(repository, hub, target, "MCP Target")
    _enroll(repository, hub, caller, "Task Node")
    resource = repository.put_workspace_resource(
        {
            "resource_id": "jira",
            "kind": "mcp",
            "generation": 3,
            "canonical_digest": "c" * 64,
            "display_name": "Jira MCP",
            "spec": {"transport": "stdio", "secret_refs": ["jira-token"]},
            "enabled": True,
        },
        created_by="subject_owner",
    )
    deployment = repository.put_deployment(
        {
            "deployment_id": "jira-on-target",
            "kind": "mcp",
            "resource_id": "jira",
            "resource_generation": 3,
            "resource_digest": "c" * 64,
            "target_node_id": target.node_id,
            "desired_generation": 7,
            "spec": {"restart_policy": "on_failure"},
            "enabled": True,
        }
    )
    grant = repository.put_resource_grant(
        {
            "grant_id": "task-node-can-call-jira",
            "caller_node_id": caller.node_id,
            "target_deployment_id": deployment["deployment_id"],
            "capability": "mcp_invoke",
            "max_request_deadline": 60,
            "expires_at": now + 3600,
        }
    )

    assert resource["spec"]["secret_refs"] == ["jira-token"]
    assert deployment["kind"] == "mcp"
    assert deployment["target_node_id"] == target.node_id
    assert grant["capability"] == "mcp_invoke"
    assert repository.active_resource_grant(
        caller.node_id,
        deployment["deployment_id"],
        capability="mcp_invoke",
    )["grant_id"] == grant["grant_id"]


def test_workspace_resource_grant_can_be_revoked(tmp_path: Path) -> None:
    repository = HubRepository(tmp_path / "hub.db", hub_id="workspace-1")
    hub = HubService(repository, tmp_path / "hub.key", owner_token="o" * 43)
    target = NodeIdentityStore(tmp_path / "target.json").load_or_create()
    caller = NodeIdentityStore(tmp_path / "caller.json").load_or_create()
    _enroll(repository, hub, target, "Target")
    _enroll(repository, hub, caller, "Caller")
    repository.put_workspace_resource(
        {
            "resource_id": "model",
            "kind": "model",
            "generation": 1,
            "canonical_digest": "a" * 64,
            "display_name": "Model",
            "spec": {},
            "enabled": True,
        },
        created_by="owner",
    )
    repository.put_deployment(
        {
            "deployment_id": "model-deployment",
            "kind": "model",
            "resource_id": "model",
            "resource_generation": 1,
            "resource_digest": "a" * 64,
            "target_node_id": target.node_id,
            "desired_generation": 1,
            "spec": {},
            "enabled": True,
        }
    )
    repository.put_resource_grant(
        {
            "grant_id": "grant-model",
            "caller_node_id": caller.node_id,
            "target_deployment_id": "model-deployment",
            "capability": "model_inference",
            "max_request_deadline": 60,
            "expires_at": time.time() + 3600,
        }
    )

    revoked = repository.revoke_resource_grant("grant-model")

    assert revoked["revoked_at"] is not None
    with pytest.raises(PermissionError):
        repository.active_resource_grant(caller.node_id, "model-deployment")


def test_node_signed_work_projection_is_monotonic_and_workspace_readable(
    tmp_path: Path,
) -> None:
    now = time.time()
    repository = HubRepository(tmp_path / "hub.db", hub_id="workspace-1")
    hub = HubService(repository, tmp_path / "hub.key", owner_token="o" * 43)
    node = NodeIdentityStore(tmp_path / "node.json").load_or_create()
    _enroll(repository, hub, node, "Task Node")
    projection = {
        "node_id": node.node_id,
        "entity_kind": "task",
        "entity_id": "task-1",
        "principal_id": "owner",
        "title": "Sync Jira",
        "state": "running",
        "progress": 0.5,
        "summary": "Reading issues",
        "approval_summary": "",
        "artifact_refs": [],
        "source_generation": 2,
        "source_digest": "d" * 64,
        "projection_seq": 10,
        "source_created_at": now - 10,
        "source_updated_at": now,
        "payload": {"latest_execution_id": "execution-1"},
        "observed_at": now,
    }
    transcript = {
        "audience": "knoa-work-projection-v1",
        "workspace_id": "workspace-1",
        **projection,
    }
    first = hub.publish_work_projection(
        {**projection, "signature": node.sign(canonical_json(transcript))}
    )
    stale = {**projection, "state": "queued", "projection_seq": 9}
    stale_transcript = {
        "audience": "knoa-work-projection-v1",
        "workspace_id": "workspace-1",
        **stale,
    }
    second = hub.publish_work_projection(
        {**stale, "signature": node.sign(canonical_json(stale_transcript))}
    )

    assert first["node_id"] == node.node_id
    assert second["state"] == "running"
    assert repository.list_work_projections(entity_kind="task")[0]["payload"] == {
        "latest_execution_id": "execution-1"
    }
    reconcile = {
        "node_id": node.node_id,
        "entity_kind": "task",
        "principal_id": "owner",
        "active_entity_ids": [],
        "observed_at": now,
    }
    reconcile_transcript = {
        "audience": "knoa-work-projection-reconcile-v1",
        "workspace_id": "workspace-1",
        **reconcile,
    }
    assert hub.reconcile_work_projections({
        **reconcile,
        "signature": node.sign(canonical_json(reconcile_transcript)),
    }) == 1
    assert repository.list_work_projections(entity_kind="task") == ()


def test_hosted_hub_resource_ticket_distinguishes_hub_and_workspace_ids(
    tmp_path: Path,
) -> None:
    hub, target, _caller, ticket = _resource_ticket(
        tmp_path,
        hub_id="hub-hosted",
        workspace_id="workspace-1",
    )

    claims = verify_resource_ticket(
        ticket,
        hub.signing_public_key,
        expected_hub_id="hub-hosted",
        expected_workspace_id="workspace-1",
        expected_target_node_id=target.node_id,
    )

    assert claims.hub_id == "hub-hosted"
    assert claims.target_direct_gateway_url == "https://node-target.example.test"
    assert claims.workspace_id == "workspace-1"


def test_resource_ticket_binds_workspace_grant_deployment_and_live_observation(
    tmp_path: Path,
) -> None:
    hub, target, caller, ticket = _resource_ticket(tmp_path)

    first = verify_resource_ticket(
        ticket,
        hub.signing_public_key,
        expected_hub_id="workspace-1",
        expected_target_node_id=target.node_id,
    )
    second = hub.verify_resource_ticket(ticket)

    assert first.caller_node_id == caller.node_id
    assert first.target_deployment_id == "qwen_local"
    assert first.max_deadline == 300
    assert second["ticket_id"] == first.ticket_id


def test_resource_handshake_authenticates_both_nodes_and_encrypts_both_directions(
    tmp_path: Path,
) -> None:
    hub, target, caller, ticket = _resource_ticket(tmp_path)
    pending = create_resource_client_hello(
        caller,
        ticket,
        hub.signing_public_key,
        expected_hub_id="workspace-1",
    )

    server, target_cipher = accept_resource_client_hello(
        pending.hello,
        session_id=pending.claims.ticket_id,
        hub_id="workspace-1",
        hub_signing_public_key=hub.signing_public_key,
        node_identity=target,
    )
    caller_cipher = finish_resource_client_handshake(
        pending,
        server,
        session_id=pending.claims.ticket_id,
    )

    sequence, ciphertext = caller_cipher.encrypt({"type": "request_end"})
    assert target_cipher.decrypt(sequence, ciphertext) == {"type": "request_end"}
    sequence, ciphertext = target_cipher.encrypt({"type": "response_end"})
    assert caller_cipher.decrypt(sequence, ciphertext) == {"type": "response_end"}


def test_invocation_repository_replays_terminal_result_without_readmission(
    tmp_path: Path,
) -> None:
    repository = RemoteModelInvocationRepository(tmp_path / "invocations.db")
    first, created = repository.admit(
        "inv-1",
        request_digest="a" * 64,
        deployment_id="deployment-1",
        materialized_digest="b" * 64,
    )
    repository.append(
        "inv-1",
        ProviderChunk(content_delta="hello"),
    )
    repository.append(
        "inv-1",
        ProviderChunk(finish_reason="stop", terminal=True),
    )
    second, admitted_again = repository.admit(
        "inv-1",
        request_digest="a" * 64,
        deployment_id="deployment-1",
        materialized_digest="b" * 64,
    )

    assert created is True
    assert admitted_again is False
    assert first["execution_epoch"] == second["execution_epoch"]
    assert [chunk.content_delta for chunk in repository.chunks("inv-1")] == [
        "hello",
        "",
    ]


@pytest.mark.asyncio
async def test_remote_endpoint_executes_same_invocation_only_once(tmp_path: Path) -> None:
    now = time.time()
    repository = HubRepository(tmp_path / "hub.db", hub_id="workspace-1")
    hub = HubService(
        repository,
        tmp_path / "hub.key",
        owner_token="o" * 43,
    )
    target = NodeIdentityStore(tmp_path / "target.json").load_or_create()
    caller = NodeIdentityStore(tmp_path / "caller.json").load_or_create()
    _enroll(repository, hub, target, "Target")
    _enroll(repository, hub, caller, "Caller")
    bootstrap = AppConfig(runtime_root=str(tmp_path / "runtime"), fallback_enabled=False)
    managed = bootstrap.managed_config()
    managed = managed.model_copy(
        update={
            "model_deployments": {
                "qwen_local": ManagedModelDeploymentConfig(
                    model_alias=managed.default_model,
                    resource_id="personal_model",
                    enabled=True,
                    share_enabled=True,
                    max_remote_concurrency=1,
                )
            }
        }
    )
    repository.put_workspace_resource(
        {
            "resource_id": "personal_model",
            "kind": "model",
            "generation": 1,
            "canonical_digest": "a" * 64,
            "display_name": "Qwen",
            "spec": {
                "provider_protocol": "openai_compatible",
                "model_identity": "qwen",
                "declared_capabilities": {},
            },
            "enabled": True,
        },
        created_by="subject_owner",
    )
    repository.put_deployment(
        {
            "deployment_id": "qwen_local",
            "kind": "model",
            "resource_id": "personal_model",
            "resource_generation": 1,
            "resource_digest": "a" * 64,
            "target_node_id": target.node_id,
            "desired_generation": 1,
            "spec": {"max_remote_concurrency": 1},
            "enabled": True,
        }
    )
    repository.put_resource_grant(
        {
            "grant_id": "grant-1",
            "caller_node_id": caller.node_id,
            "target_deployment_id": "qwen_local",
            "max_request_deadline": 300,
            "expires_at": now + 3600,
        }
    )
    digest = materialized_deployment_digest(managed, "qwen_local")
    observation = {
        "node_id": target.node_id,
        "deployment_id": "qwen_local",
        "applied_digest": digest,
        "health_epoch": 1,
        "health": "healthy",
        "capabilities": {},
        "available_capacity": 1,
        "observed_at": now,
        "expires_at": now + 90,
    }
    hub.publish_deployment_observation(
        {
            **observation,
            "signature": target.sign(
                canonical_json(
                    {
                        "audience": "knoa-deployment-observation-v1",
                        "workspace_id": "workspace-1",
                        **observation,
                    }
                )
            ),
        }
    )
    ticket_request = {
        "invocation_id": "invocation-1",
        "caller_node_id": caller.node_id,
        "target_deployment_id": "qwen_local",
        "max_deadline": 300,
        "timestamp": now,
        "nonce": "z" * 24,
    }
    ticket = hub.issue_resource_ticket(
        {
            **ticket_request,
            "signature": caller.sign(
                canonical_json(
                    {
                        "audience": "knoa-resource-ticket-request-v1",
                        "workspace_id": "workspace-1",
                        **ticket_request,
                    }
                )
            ),
        }
    )
    hub_store = NodeHubStore(tmp_path / "runtime" / "data" / "node-hub.json")
    hub_store.save(
        hub_url="https://hub.example.test",
        hub_id="workspace-1",
        hub_signing_public_key=hub.signing_public_key,
    )

    class _Core:
        async def get_config_current(self, _principal_id: str):
            return SimpleNamespace(document=managed), None, ()

    calls = 0

    class _Provider:
        def __init__(self, _model) -> None:
            pass

        def stream(self, _request, _cancellation):
            async def generate():
                nonlocal calls
                calls += 1
                yield ProviderChunk(content_delta="hello")
                yield ProviderChunk(finish_reason="stop", terminal=True)

            return generate()

    endpoint = RemoteModelEndpoint(
        RemoteModelInvocationRepository(tmp_path / "invocations.db"),
        core=_Core(),
        bootstrap=bootstrap,
        paths=RuntimePaths.from_root(bootstrap.runtime_root),
        identity=target,
        hub_store=hub_store,
        provider_factory=_Provider,
    )
    request = ProviderCallRequest(
        call_id="call-1",
        purpose="react",
        messages=({"role": "user", "content": "hello"},),
    )

    first = await endpoint.invoke("invocation-1", ticket, request)
    second = await endpoint.invoke("invocation-1", ticket, request)

    assert calls == 1
    assert first == second
    assert first[-1].terminal is True


@pytest.mark.asyncio
async def test_remote_provider_reuses_invocation_when_direct_falls_back_to_relay(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model = ResolvedModelConfig(
        alias="shared_qwen",
        provider_name="workspace",
        driver="workspace_remote",
        server_url="",
        api_base="",
        api_key="",
        model="qwen",
        supports_vision=False,
        context_window=8192,
        timeout=120,
        remote_deployment_id="qwen_local",
        direct_gateway_url="https://node-a.example.test",
    )
    provider = RemoteModelProvider(
        model,
        paths=RuntimePaths.from_root(tmp_path / "runtime"),
    )
    seen: dict[str, str] = {}

    async def issue(invocation_id: str) -> tuple[str, str]:
        seen["issued"] = invocation_id
        return "ticket", "https://node-current.example.test"

    async def direct(invocation_id: str, _body: dict, direct_gateway_url: str) -> dict:
        seen["direct"] = invocation_id
        assert direct_gateway_url == "https://node-current.example.test"
        raise httpx.ConnectError("offline")

    async def relay(invocation_id: str, ticket: str, _body: dict) -> dict:
        seen["relay"] = invocation_id
        assert ticket == "ticket"
        return {
            "chunks": [
                ProviderChunk(content_delta="hello").model_dump(mode="json"),
                ProviderChunk(
                    finish_reason="stop", terminal=True
                ).model_dump(mode="json"),
            ]
        }

    monkeypatch.setattr(provider, "_issue_ticket", issue)
    monkeypatch.setattr(provider, "_direct", direct)
    monkeypatch.setattr(provider, "_relay", relay)
    request = ProviderCallRequest(
        call_id="call-1",
        purpose="react",
        messages=({"role": "user", "content": "hello"},),
    )

    chunks = [
        chunk
        async for chunk in provider.stream(request, asyncio.Event())
    ]

    assert seen["issued"] == seen["direct"] == seen["relay"]
    assert chunks[-1].terminal is True
