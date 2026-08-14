from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import ClassVar

import pytest

from knoa_platform.agent_runtime.composition import (
    PERSONAL_LOCAL_CAPABILITIES,
    REMOTE_SCOPED_CAPABILITIES,
    build_core_runtime,
)
from knoa_platform.agent_runtime.contracts import (
    HealthStatus,
    RuntimeScope,
)
from knoa_platform.agent_runtime.model_step import ProviderChunk
from knoa_platform.agents import ExecuteAgentTurn
from knoa_platform.config import AppConfig
from knoa_platform.runtime import RuntimePaths
from knoa_platform.service.core_client import CoreClient
from knoa_platform.service.credentials import (
    issue_principal_credential,
    resolve_local_service_token,
)
from knoa_platform.service.product_task_lifecycle import ProductTaskLifecycle
from knoa_platform.tasks import TaskDefinitionState, TaskLaunchKind, TaskLaunchPolicy


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


class _CaptureProvider(_OfflineProvider):
    instances: ClassVar[list[_CaptureProvider]] = []

    def __init__(self, model) -> None:
        super().__init__(model)
        self.requests = []
        type(self).instances.append(self)

    def stream(self, request, cancellation):
        del cancellation

        async def answer_stream():
            self.requests.append(request)
            yield ProviderChunk(content_delta="done")
            yield ProviderChunk(
                finish_reason="stop",
                usage={"prompt_tokens": 321, "completion_tokens": 17},
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


@pytest.mark.asyncio
async def test_knoa_provider_request_receives_scoped_memory_in_tail_context(
    tmp_path: Path,
) -> None:
    _CaptureProvider.instances = []
    composition = build_core_runtime(
        _config(tmp_path, service_port=0),
        provider_factory=_CaptureProvider,
    )
    scope_a = await composition.control.create_session("principal-a")
    scope_b = await composition.control.create_session("principal-b")
    composition.memory.store_memory(
        "principal-a",
        "preferred_language",
        "zh",
        category="communication",
        importance="core",
        confidence=1.0,
        source="explicit",
    )
    composition.memory.store_memory(
        "principal-a",
        "preferred_editor",
        "vim",
        category="preference",
        importance="relevant",
        confidence=1.0,
        source="explicit",
    )
    composition.memory.store_memory(
        "principal-a",
        "unrelated_workflow",
        "never injected",
        category="workflow",
        importance="relevant",
        confidence=1.0,
        source="explicit",
    )

    async def run(scope, turn_id, text):
        return [
            event
            async for event in composition.agent_execution.execute_turn(
                ExecuteAgentTurn(
                    scope=scope,
                    turn_id=turn_id,
                    client_request_id=f"request-{turn_id}",
                    input=text,
                    attachments=(),
                    tools_enabled=False,
                    cancellation=asyncio.Event(),
                )
            )
        ]

    await run(scope_a, "turn-a", "Use my editor preference")
    await run(scope_b, "turn-b", "Use my editor preference")

    provider = _CaptureProvider.instances[0]
    first = provider.requests[0].messages
    assert first[-1]["content"][0]["text"] == "Use my editor preference"
    runtime_context = first[-2]["content"]
    assert "preferred_language: zh" in runtime_context
    assert "preferred_editor: vim" in runtime_context
    assert "unrelated_workflow" not in runtime_context
    assert "preferred_language: zh" not in str(provider.requests[1].messages)

    model_trace = composition.llm_traces.recent(2)[0]
    assert model_trace["prompt_tokens"] == 321
    assert model_trace["completion_tokens"] == 17
    assert model_trace["prompt_tokens_source"] == "provider"
    assert model_trace["completion_tokens_source"] == "provider"
    assert model_trace["prompt_tokens_estimated"] > 0


def test_core_composition_builds_forward_only_registry_and_profiles(
    tmp_path: Path,
) -> None:
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
        "mcp_deploy",
        "mcp_connect",
        "mcp_inspect",
        "mcp_disable",
        "tool_help",
    } <= local
    assert {"web_search", "web_fetch", "weather", "currency"} <= remote
    assert (
        not {
            "attach",
            "read_file",
            "write_file",
            "run_command",
            "mouse",
            "tool_help",
        }
        & remote
    )
    assert not {"screen", "ui", "schedule", "inspect_image"} & local
    assert "create_task" in local
    assert "schedule_task" not in local


def test_builtin_agent_tool_contracts_are_english_only(tmp_path: Path) -> None:
    composition = build_core_runtime(
        _config(tmp_path, service_port=0),
        provider_factory=_OfflineProvider,
    )

    rendered = json.dumps(
        {
            name: composition.registry.detailed_schema(name)
            for name in composition.registry.list_tools()
        },
        ensure_ascii=False,
    )

    assert (
        re.search(
            r"[\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af]",
            rendered,
        )
        is None
    )


def test_codex_uses_private_neutral_workspace_when_cwd_is_empty(
    tmp_path: Path,
) -> None:
    composition = build_core_runtime(
        _config(
            tmp_path,
            service_port=0,
            agents={
                "knoa": {"enabled": True},
                "codex": {
                    "enabled": True,
                    "command": ["codex", "app-server"],
                    "cwd": "",
                },
            },
        ),
        provider_factory=_OfflineProvider,
    )

    codex = composition.agent_manager.runtime("codex")
    assert codex._cwd == str(
        (tmp_path / "runtime" / "agents" / "codex" / "workspace").resolve()
    )
    assert Path(codex._cwd).is_dir()
    assert Path(codex._cwd) != (tmp_path / "workspace").resolve()


@pytest.mark.asyncio
async def test_agent_task_lifecycle_keeps_schedule_and_task_in_sync(
    tmp_path: Path,
) -> None:
    composition = build_core_runtime(
        _config(tmp_path, service_port=0),
        provider_factory=_OfflineProvider,
    )
    scope = RuntimeScope(
        principal_id="personal:owner",
        session_handle="chat-a",
    )
    create = composition.registry.get("create_task")
    task = composition.registry.get("task")
    assert create is not None
    assert task is not None

    created = await create.execute_scoped(
        scope,
        title="Evening review",
        goal="Review today's notes.",
        launch={
            "kind": "cron",
            "cron": "30 18 * * *",
            "timezone": "Asia/Shanghai",
        },
    )
    task_id = created["task_id"]
    original_binding = await composition.task_service.launch_binding(
        scope.principal_id,
        task_id,
    )
    assert original_binding is not None

    paused = await task.execute_scoped(
        scope,
        action="pause",
        task_id=task_id,
    )
    updated = await task.execute_scoped(
        scope,
        action="update",
        task_id=task_id,
        launch={
            "kind": "cron",
            "cron": "0 19 * * *",
            "timezone": "Asia/Shanghai",
        },
    )
    replacement_binding = await composition.task_service.launch_binding(
        scope.principal_id,
        task_id,
    )

    assert paused["state"] == "paused"
    assert paused["launch_state"] == "paused"
    assert updated["launch"] == {
        "kind": "cron",
        "cron": "0 19 * * *",
        "timezone": "Asia/Shanghai",
    }
    assert updated["launch_state"] == "paused"
    assert replacement_binding is not None
    assert replacement_binding != original_binding
    with pytest.raises(LookupError):
        await composition.schedule_service.get(
            scope.principal_id,
            original_binding[1],
        )

    resumed = await task.execute_scoped(
        scope,
        action="resume",
        task_id=task_id,
    )
    deleted = await task.execute_scoped(
        scope,
        action="delete",
        task_id=task_id,
    )

    assert resumed["state"] == "active"
    assert resumed["launch_state"] == "active"
    assert deleted == {"task_id": task_id, "deleted": True}
    with pytest.raises(LookupError):
        await composition.schedule_service.get(
            scope.principal_id,
            replacement_binding[1],
        )


@pytest.mark.asyncio
async def test_shared_task_lifecycle_keeps_event_trigger_in_sync(
    tmp_path: Path,
) -> None:
    composition = build_core_runtime(
        _config(tmp_path, service_port=0),
        provider_factory=_OfflineProvider,
    )
    lifecycle = ProductTaskLifecycle(
        composition.task_service,
        composition.schedule_service,
        composition.trigger_service,
    )
    scope = composition.sessions.create("personal:owner", activate=False)

    task, execution, provider = await lifecycle.create_definition(
        scope,
        client_request_id="event-task-a",
        title="Webhook import",
        goal="Import and summarize the webhook payload.",
        launch_policy=TaskLaunchPolicy(
            kind=TaskLaunchKind.EVENT,
            event_source="webhook",
        ),
    )
    original_binding = await composition.task_service.launch_binding(
        scope.principal_id,
        task.task_id,
    )
    assert execution is None
    assert provider.state == "active"
    assert original_binding is not None
    assert original_binding[0] == "event"

    paused = await lifecycle.set_definition_state(
        scope.principal_id,
        task.task_id,
        TaskDefinitionState.PAUSED,
    )
    updated = await lifecycle.update_definition(
        scope.principal_id,
        task.task_id,
        title="Renamed webhook import",
    )
    replacement_binding = await composition.task_service.launch_binding(
        scope.principal_id,
        task.task_id,
    )

    assert paused.state is TaskDefinitionState.PAUSED
    assert updated.title == "Renamed webhook import"
    assert replacement_binding is not None
    assert replacement_binding != original_binding
    replacement = await composition.trigger_service.get(
        scope.principal_id,
        replacement_binding[1],
    )
    assert replacement.state.value == "paused"
    with pytest.raises(LookupError):
        await composition.trigger_service.get(
            scope.principal_id,
            original_binding[1],
        )

    await lifecycle.delete_definition(scope.principal_id, task.task_id)
    with pytest.raises(LookupError):
        await composition.trigger_service.get(
            scope.principal_id,
            replacement_binding[1],
        )


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
async def test_tcp_endpoint_separates_local_and_remote_credentials(
    tmp_path: Path,
) -> None:
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

    assert [event.event_type for event in events] == [
        "task_created",
        "state_changed",
        "completed",
    ]
    execution_trace = composition.tasks.get_trace("local", task.task_id)
    assert execution_trace is not None
    assert execution_trace.final_output == "done"

    model_trace = composition.llm_traces.recent(1)[0]
    turn_trace = composition.turn_traces.recent(1)[0]
    assert model_trace["run_hash"] == turn_trace["run_hash"]
    assert model_trace["session_hash"] == turn_trace["session_hash"]
    assert model_trace["client_request_hash"] == turn_trace["client_request_hash"]
    assert "run_id" not in model_trace
    assert "client_request_id" not in model_trace
    assert model_trace["cached_tokens"] == 3
    assert model_trace["schema_tokens"] > 0
    assert turn_trace["outcome"] == "completed"
    assert turn_trace["iterations"] == 1
    assert "user_input" not in turn_trace
