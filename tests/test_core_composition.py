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
    RunRequest,
    RuntimeScope,
)
from pc_assistant.agent_runtime.model_step import ProviderChunk
from pc_assistant.config import AppConfig


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
        _config(tmp_path),
        socket_path=tmp_path / "core.sock",
        provider_factory=_OfflineProvider,
    )

    local = set(composition.registry.list_for(PERSONAL_LOCAL_CAPABILITIES))
    remote = set(composition.registry.list_for(REMOTE_SCOPED_CAPABILITIES))

    assert {"read_file", "write_file", "screenshot", "mouse", "tool_help"} <= local
    assert {"web_search", "web_fetch", "weather", "currency"} <= remote
    assert not {"read_file", "write_file", "run_command", "mouse", "tool_help"} & remote
    assert not {"screen", "ui", "schedule", "inspect_image"} & local


@pytest.mark.asyncio
async def test_control_lists_only_principal_profile_tools(tmp_path: Path) -> None:
    composition = build_core_runtime(
        _config(tmp_path),
        socket_path=tmp_path / "core.sock",
        provider_factory=_OfflineProvider,
    )
    local = await composition.control.create_session("local")
    remote = await composition.control.create_session("remote")

    local_tools = set((await composition.control.list_tools(local)).tools)
    remote_tools = set((await composition.control.list_tools(remote)).tools)

    assert "screenshot" in local_tools
    assert "screenshot" not in remote_tools
    assert remote_tools == {"currency", "weather", "web_fetch", "web_search"}
    assert RuntimeScope(principal_id="local", session_handle=local.session_handle) == local


def test_tcp_endpoint_requires_token_even_on_loopback(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="requires an authentication token"):
        build_core_runtime(
            _config(tmp_path, service_host="127.0.0.1", service_port=8765),
            socket_path=tmp_path / "core.sock",
            provider_factory=_OfflineProvider,
        )


@pytest.mark.asyncio
async def test_composition_records_correlated_model_and_turn_traces(
    tmp_path: Path,
) -> None:
    composition = build_core_runtime(
        _config(tmp_path),
        socket_path=tmp_path / "core.sock",
        provider_factory=_AnswerProvider,
    )
    scope = await composition.control.create_session("local")

    _events = [
        event
        async for event in composition.application.run(
            "local",
            scope.session_handle,
            RunRequest(client_request_id="request-a", input="hello"),
        )
    ]

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
