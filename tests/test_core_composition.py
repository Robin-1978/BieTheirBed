from __future__ import annotations

from pathlib import Path

import pytest

from pc_assistant.agent_runtime.composition import (
    PERSONAL_LOCAL_CAPABILITIES,
    REMOTE_SCOPED_CAPABILITIES,
    build_core_runtime,
)
from pc_assistant.agent_runtime.contracts import (
    HealthStatus,
    RuntimeScope,
)
from pc_assistant.agent_runtime.model_step import ProviderChunk
from pc_assistant.config import AppConfig
from pc_assistant.runtime import RuntimePaths
from pc_assistant.service.core_client import CoreClient
from pc_assistant.service.credentials import (
    issue_principal_credential,
    resolve_local_service_token,
)


class _OfflineProvider:
    def __init__(self, model) -> None:
        self.model_alias = model.alias

    async def health_check(self) -> HealthStatus:
        return HealthStatus(healthy=True, detail=self.model_alias)

    def stream(self, request, cancellation):
        del request, cancellation

        async def empty_stream():
            if False:
                yield

        return empty_stream()


class _AnswerProvider(_OfflineProvider):
    def stream(self, request, cancellation):
        del request, cancellation

        async def answer_stream():
            yield ProviderChunk(content_delta="done")
            yield ProviderChunk(
                finish_reason="stop",
                usage={
                    "prompt_tokens": 10,
                    "completion_tokens": 2,
                    "prompt_tokens_details": {"cached_tokens": 3},
                },
                terminal=True,
            )

        return answer_stream()


def _config(tmp_path: Path, **updates) -> AppConfig:
    values = {
        "runtime_root": str(tmp_path / "runtime"),
        "working_directory": str(tmp_path / "workspace"),
        "fallback_enabled": False,
    }
    values.update(updates)
    return AppConfig(**values)


def test_core_composition_builds_forward_only_registry_and_profiles(tmp_path: Path) -> None:
    composition = build_core_runtime(
        _config(tmp_path, service_port=0),
        provider_factory=_OfflineProvider,
    )

    local = set(composition.registry.list_for(PERSONAL_LOCAL_CAPABILITIES))
    remote = set(composition.registry.list_for(REMOTE_SCOPED_CAPABILITIES))

    assert {
        "attach",
        "read_file",
        "write_file",
        "screenshot",
        "mouse",
        "mcp_import",
        "tool_help",
    } <= local
    assert {"web_search", "web_fetch", "weather", "currency"} <= remote
    assert not {
        "attach",
        "read_file",
        "write_file",
        "run_command",
        "mouse",
        "tool_help",
    } & remote
    assert not {"screen", "ui", "schedule", "inspect_image"} & local


@pytest.mark.asyncio
async def test_control_lists_only_principal_profile_tools(tmp_path: Path) -> None:
    composition = build_core_runtime(
        _config(tmp_path, service_port=0),
        provider_factory=_OfflineProvider,
    )
    local = await composition.control.create_session("personal:owner")
    remote = await composition.control.create_session("remote")

    local_result = await composition.control.list_tools(local)
    remote_result = await composition.control.list_tools(remote)
    local_tools = set(local_result.tools)
    remote_tools = set(remote_result.tools)

    assert "screenshot" in local_tools
    assert "screenshot" not in remote_tools
    assert remote_tools == {
        "currency",
        "read_artifact",
        "weather",
        "web_fetch",
        "web_search",
    }
    local_descriptors = {item.name: item for item in local_result.descriptors}
    assert set(local_descriptors) == local_tools
    assert local_descriptors["read_file"].origin_kind == "builtin"
    assert local_descriptors["read_file"].effect == "read_only"
    assert local_descriptors["write_file"].requires_confirmation is True
    assert {item.name for item in remote_result.descriptors} == remote_tools
    assert (
        RuntimeScope(
            principal_id="personal:owner",
            session_handle=local.session_handle,
        )
        == local
    )


def test_tcp_endpoint_uses_managed_token_when_not_configured(tmp_path: Path) -> None:
    composition = build_core_runtime(
        _config(tmp_path, service_host="127.0.0.1", service_port=0),
        provider_factory=_OfflineProvider,
    )

    token_path = composition.paths.config / "service.token"
    assert token_path.read_text(encoding="utf-8").strip()


@pytest.mark.asyncio
async def test_tcp_endpoint_separates_local_and_remote_credentials(tmp_path: Path) -> None:
    config = _config(
        tmp_path,
        service_host="127.0.0.1",
        service_port=0,
        service_token="remote-secret",
    )
    composition = build_core_runtime(config, provider_factory=_OfflineProvider)
    await composition.task_service.start()
    await composition.host.start()
    local_client: CoreClient | None = None
    remote_client: CoreClient | None = None
    channel_client: CoreClient | None = None
    try:
        uri = f"ws://127.0.0.1:{composition.host.bound_tcp_port}"
        local_client = await CoreClient.connect(
            uri,
            resolve_local_service_token(RuntimePaths.from_root(config.runtime_root)),
        )
        remote_client = await CoreClient.connect(uri, "remote-secret")
        channel_client = await CoreClient.connect(
            uri,
            issue_principal_credential(
                resolve_local_service_token(
                    RuntimePaths.from_root(config.runtime_root)
                ),
                "personal:channel:user-a",
            ),
        )
        local_session = await local_client.create_session()
        remote_session = await remote_client.create_session()
        channel_session = await channel_client.create_session()

        local_tools = set((await local_client.list_tools(local_session)).tools)
        remote_tools = set((await remote_client.list_tools(remote_session)).tools)
        channel_tools = set((await channel_client.list_tools(channel_session)).tools)

        assert "screenshot" in local_tools
        assert "screenshot" not in remote_tools
        assert remote_tools == {
            "currency",
            "read_artifact",
            "weather",
            "web_fetch",
            "web_search",
        }
        assert "screenshot" in channel_tools
        assert "mouse" in channel_tools
    finally:
        if local_client is not None:
            await local_client.disconnect()
        if remote_client is not None:
            await remote_client.disconnect()
        if channel_client is not None:
            await channel_client.disconnect()
        await composition.host.stop()
        await composition.task_service.stop()


@pytest.mark.asyncio
async def test_new_session_status_uses_configured_model_alias(tmp_path: Path) -> None:
    composition = build_core_runtime(
        _config(tmp_path, service_port=0),
        provider_factory=_OfflineProvider,
    )
    scope = await composition.control.create_session("local")

    status = await composition.control.get_status(scope)

    assert status.status == "ready"
    assert status.details["model"] == "default"
    assert status.details["model_calls"] == 0
    assert status.details["total_tokens"] == 0


@pytest.mark.asyncio
async def test_broken_local_mcp_package_does_not_block_core(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime"
    package = runtime_root / "mcp" / "broken"
    package.mkdir(parents=True)
    (package / "mcp.yaml").write_text(
        "enabled: true\ntransport: stdio\ncommand: ''\n",
        encoding="utf-8",
    )
    composition = build_core_runtime(
        _config(tmp_path, service_port=0),
        provider_factory=_OfflineProvider,
    )

    await composition.extensions.start()
    try:
        failed = next(
            status
            for status in composition.extensions.statuses
            if status.descriptor.extension_id == "mcp:broken"
        )
        assert failed.state.value == "failed"
        assert "read_file" in composition.registry.list_tools()
    finally:
        await composition.extensions.stop()


@pytest.mark.asyncio
async def test_composition_records_correlated_model_and_turn_traces(
    tmp_path: Path,
) -> None:
    composition = build_core_runtime(
        _config(tmp_path, service_port=0),
        provider_factory=_AnswerProvider,
    )
    scope = await composition.control.create_session("local")
    await composition.task_service.start()
    try:
        task = await composition.task_service.create(
            scope,
            client_request_id="request-a",
            goal="hello",
        )
        events = [
            event
            async for event in composition.task_service.events(
                "local",
                task.task_id,
            )
        ]
    finally:
        await composition.task_service.stop()

    final = [event for event in events if event.event_type == "final_output"]
    assert len(final) == 1
    assert final[0].payload.content == "done"

    model_trace = composition.llm_traces.recent(1)[0]
    turn_trace = composition.turn_traces.recent(1)[0]
    assert model_trace["run_hash"] == turn_trace["run_hash"]
    assert model_trace["session_hash"] == turn_trace["session_hash"]
    assert (
        model_trace["client_request_hash"]
        == turn_trace["client_request_hash"]
    )
    assert "run_id" not in model_trace
    assert "client_request_id" not in model_trace
    assert model_trace["cached_tokens"] == 3
    assert model_trace["schema_tokens"] > 0
    assert turn_trace["outcome"] == "completed"
    assert turn_trace["iterations"] == 1
    assert "user_input" not in turn_trace
