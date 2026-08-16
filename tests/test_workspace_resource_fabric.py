from __future__ import annotations

import asyncio
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
) -> tuple[HubService, NodeIdentity, NodeIdentity, str]:
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
    repository.put_model_resource(
        {
            "resource_id": "personal_model",
            "revision": 1,
            "canonical_digest": "a" * 64,
            "display_name": "Qwen 3.5 4B",
            "provider_protocol": "openai_compatible",
            "model_identity": "qwen3.5-4b",
            "declared_capabilities": {"streaming": True},
        },
        created_by="subject_owner",
    )
    repository.put_model_deployment(
        {
            "deployment_id": "qwen_local",
            "resource_id": "personal_model",
            "resource_revision": 1,
            "target_node_id": target.node_id,
            "desired_revision": 1,
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
        "workspace_id": "workspace-1",
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
        "workspace_id": "workspace-1",
        **request,
    }
    ticket = hub.issue_resource_ticket(
        {
            **request,
            "signature": caller.sign(canonical_json(request_transcript)),
        }
    )
    return hub, target, caller, ticket


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
    repository.put_model_resource(
        {
            "resource_id": "personal_model",
            "revision": 1,
            "canonical_digest": "a" * 64,
            "display_name": "Qwen",
            "provider_protocol": "openai_compatible",
            "model_identity": "qwen",
            "declared_capabilities": {},
        },
        created_by="subject_owner",
    )
    repository.put_model_deployment(
        {
            "deployment_id": "qwen_local",
            "resource_id": "personal_model",
            "resource_revision": 1,
            "target_node_id": target.node_id,
            "desired_revision": 1,
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

    async def issue(invocation_id: str) -> str:
        seen["issued"] = invocation_id
        return "ticket"

    async def direct(invocation_id: str, _body: dict) -> dict:
        seen["direct"] = invocation_id
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
