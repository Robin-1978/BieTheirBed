from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from pc_assistant.config import AppConfig
from pc_assistant.context.session_db import SessionTranscriptRepository
from pc_assistant.harness.audit import AuditLogger
from pc_assistant.harness.idempotency import IdempotencyLog
from pc_assistant.harness.executor import VerifiedToolExecutor
from pc_assistant.harness.safety import SafetyChecker
from pc_assistant.harness.verifier import Verifier
from pc_assistant.llm_provider import LLMProvider, StreamChunk
from pc_assistant.observability.trace import LLMTraceRecorder
from pc_assistant.tools.base import ToolBase
from pc_assistant.tools.registry import ToolRegistry


def _catalog_config(root: Path, *, default_model: str = "vision") -> AppConfig:
    return AppConfig(
        runtime_root=str(root),
        providers={
            "local": {
                "driver": "llamacpp",
                "server_url": "http://127.0.0.1:8192",
                "requires_api_key": False,
            }
        },
        models={
            "vision": {
                "provider": "local",
                "model": "vision-model",
                "supports_vision": True,
                "context_window": 32768,
            },
            "text": {
                "provider": "local",
                "model": "text-model",
                "supports_vision": False,
                "context_window": 16384,
            },
        },
        default_model=default_model,
        vision_model="vision",
    )


class _CustomTool(ToolBase):
    name = "custom_generation_tool"
    description = "Factory generation test tool"

    async def execute(self, **kwargs: Any) -> Any:
        return {"ok": True}

    def schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {"type": "object", "properties": {}},
        }


# covers agent-contracts-factory#REQ-003-S01
def test_factory_builds_complete_default_dependencies(tmp_path: Path) -> None:
    from pc_assistant.agent_runtime.factory import AgentDependencies, AgentFactory

    dependencies = AgentFactory(_catalog_config(tmp_path)).build()

    assert isinstance(dependencies, AgentDependencies)
    assert dependencies.runtime_paths.root == tmp_path.resolve()
    assert dependencies.execution.resolved_model.alias == "vision"
    assert dependencies.execution.llm.supports_vision is True
    assert "read_file" in dependencies.execution.registry.list_tools()
    assert "inspect_image" not in dependencies.execution.registry.list_tools()
    assert dependencies.execution.verifier._registry is dependencies.execution.registry
    assert dependencies.execution.executor._registry is dependencies.execution.registry
    assert dependencies.execution.tool_schemas == tuple(
        item["function"] for item in dependencies.execution.registry.all_schemas()
    )


# covers agent-contracts-factory#REQ-003-S02
# covers agent-contracts-factory#REQ-004-S03
def test_factory_preserves_explicit_collaborators(tmp_path: Path) -> None:
    from pc_assistant.agent_runtime.factory import AgentFactory, FactoryOverrides

    registry = ToolRegistry()
    registry.register(_CustomTool())
    llm = LLMProvider(supports_vision=True)
    overrides = FactoryOverrides(llm=llm, registry=registry)

    dependencies = AgentFactory(
        _catalog_config(tmp_path),
        overrides=overrides,
        disable_tools=True,
    ).build()

    assert dependencies.execution.llm is llm
    assert dependencies.execution.registry is registry
    assert dependencies.execution.registry.get(_CustomTool.name) is not None
    assert dependencies.execution.registry.list_tools() == [_CustomTool.name]


def test_factory_allows_empty_registry_when_tools_are_disabled(tmp_path: Path) -> None:
    from pc_assistant.agent_runtime.factory import AgentFactory

    dependencies = AgentFactory(
        _catalog_config(tmp_path, default_model="text"),
        disable_tools=True,
    ).build()

    assert dependencies.execution.tools_enabled is False
    assert dependencies.execution.registry.list_tools() == []
    assert dependencies.execution.tool_schemas == ()


def test_factory_failure_does_not_mutate_injected_registry(tmp_path: Path) -> None:
    from pc_assistant.agent_runtime.factory import AgentFactory, FactoryOverrides

    registry = ToolRegistry()
    registry.register(_CustomTool())
    factory = AgentFactory(
        _catalog_config(tmp_path),
        overrides=FactoryOverrides(registry=registry),
    )

    with patch.object(
        factory,
        "_validate_execution_dependencies",
        side_effect=ValueError("candidate invalid"),
    ):
        with pytest.raises(ValueError, match="candidate invalid"):
            factory.build()

    assert registry.list_tools() == [_CustomTool.name]


# covers agent-contracts-factory#REQ-003-S03
def test_factory_partial_failure_returns_no_bundle(tmp_path: Path) -> None:
    from pc_assistant.agent_runtime.factory import AgentFactory

    factory = AgentFactory(_catalog_config(tmp_path))

    with patch.object(factory, "_build_llm", side_effect=RuntimeError("provider failed")):
        with pytest.raises(RuntimeError, match="provider failed"):
            factory.build()


def test_factory_rejects_mismatched_injected_executor_binding(tmp_path: Path) -> None:
    from pc_assistant.agent_runtime.factory import AgentFactory, FactoryOverrides

    registry = ToolRegistry()
    audit = AuditLogger(log_dir=str(tmp_path / "audit"))
    verifier = Verifier(SafetyChecker(), registry, audit)
    different_verifier = Verifier(SafetyChecker(), registry, audit)
    executor = VerifiedToolExecutor(different_verifier, registry)

    with pytest.raises(ValueError, match="candidate verifier"):
        AgentFactory(
            _catalog_config(tmp_path),
            overrides=FactoryOverrides(
                registry=registry,
                verifier=verifier,
                executor=executor,
            ),
            disable_tools=True,
        ).build()


# covers agent-contracts-factory#REQ-003-S04
def test_factory_reuses_runtime_paths_and_reads_pre_cutover_state(tmp_path: Path) -> None:
    from pc_assistant.agent_runtime.factory import AgentFactory

    config = _catalog_config(tmp_path)
    database = tmp_path / "data" / "assistant.db"
    transcript = SessionTranscriptRepository(database)
    transcript.save("session-before-c1", [{"role": "user", "content": "persisted"}])
    idem_path = tmp_path / "cache" / "idempotency.json"
    old_idempotency = IdempotencyLog(idem_path)
    old_idempotency.record("before-c1", {"ok": True})

    dependencies = AgentFactory(config).build()

    assert dependencies.memory_repository.path == database.resolve()
    assert dependencies.session_transcripts._path == database
    assert dependencies.session_transcripts.load("session-before-c1")[0]["content"] == "persisted"
    assert dependencies.idempotency._storage_path == idem_path
    assert dependencies.idempotency.check("before-c1") == {"ok": True}
    assert dependencies.artifact_store.root == (tmp_path / "attachments").resolve()
    assert dependencies.artifact_store.persistent_root == (tmp_path / "artifacts").resolve()
    assert dependencies.artifact_store._db_path == database.resolve()
    assert dependencies.procedural_memory._dir == tmp_path / "data" / "procedures"
    assert dependencies.audit._log_dir == tmp_path / "logs" / "audit"
    assert dependencies.trace._path == tmp_path / "logs" / "llm_calls.jsonl"
    assert dependencies.turn_recorder._path == tmp_path / "logs" / "turns.jsonl"


# covers agent-contracts-factory#REQ-004-S01
def test_agent_publishes_one_complete_execution_generation(tmp_path: Path) -> None:
    from pc_assistant.agent import Agent

    agent = Agent(config=_catalog_config(tmp_path, default_model="vision"))
    before = agent._execution_dependencies

    result = agent.switch_model("text")
    after = agent._execution_dependencies

    assert result["applied"] is True
    assert after is not before
    assert agent._dependencies.execution is after
    assert after.generation == before.generation + 1
    assert after.config.default_model == "text"
    assert after.resolved_model.alias == "text"
    assert after.llm.supports_vision is False
    assert after.vision_broker is not None
    assert "inspect_image" in after.registry.list_tools()
    assert any(schema["name"] == "inspect_image" for schema in after.tool_schemas)
    assert after.verifier._registry is after.registry
    assert after.executor._registry is after.registry


# covers agent-contracts-factory#REQ-004-S02
def test_rebuild_failure_leaves_generation_config_and_registry_unchanged(
    tmp_path: Path,
) -> None:
    from pc_assistant.agent import Agent

    agent = Agent(config=_catalog_config(tmp_path, default_model="vision"))
    before = agent._execution_dependencies
    before_config = before.config.model_dump()
    before_schemas = before.tool_schemas
    assert agent.config.set_field("default_model", "text")

    with patch.object(agent._factory, "_build_llm", side_effect=RuntimeError("boom")):
        result = agent.apply_config_change("default_model")

    assert result["applied"] is False
    assert agent._execution_dependencies is before
    assert agent._execution_dependencies.config.model_dump() == before_config
    assert agent.config.model_dump() == before_config
    assert agent.registry is before.registry
    assert agent._execution_dependencies.tool_schemas is before_schemas


# covers agent-contracts-factory#REQ-004-S04
def test_rebuild_rejects_replacing_injected_model_or_registry(tmp_path: Path) -> None:
    from pc_assistant.agent import Agent

    registry = ToolRegistry()
    llm = LLMProvider(supports_vision=True)
    agent = Agent(
        config=_catalog_config(tmp_path, default_model="vision"),
        llm=llm,
        registry=registry,
        disable_tools=True,
    )
    before = agent._execution_dependencies

    result = agent.switch_model("text")

    assert result["applied"] is False
    assert result["restart_required"] is True
    assert agent._execution_dependencies is before
    assert agent.config.default_model == "vision"
    assert agent._execution_dependencies.llm is llm
    assert agent._execution_dependencies.registry is registry


def test_rebuild_rejects_fallback_change_for_injected_model(tmp_path: Path) -> None:
    from pc_assistant.agent import Agent

    config = _catalog_config(tmp_path, default_model="vision")
    config.fallback_model = "text"
    agent = Agent(config=config, llm=LLMProvider(supports_vision=True))
    before = agent._execution_dependencies
    assert agent.config.set_field("fallback_enabled", "false")

    result = agent.apply_config_change("fallback_enabled")

    assert result["applied"] is False
    assert agent._execution_dependencies is before
    assert agent.config.fallback_enabled is True
    assert agent._execution_dependencies.resolved_fallback_model is not None


def test_vision_generation_reuses_or_rebinds_one_broker(tmp_path: Path) -> None:
    from pc_assistant.agent import Agent

    agent = Agent(config=_catalog_config(tmp_path, default_model="text"))
    before = agent._execution_dependencies
    before_tool = before.registry.get("inspect_image")
    assert before_tool is not None
    assert before_tool._broker is before.vision_broker

    assert agent.config.set_field("max_iterations", "9")
    result = agent.apply_config_change("max_iterations")
    retained = agent._execution_dependencies

    assert result["applied"] is True
    assert retained.vision_broker is before.vision_broker
    assert retained.registry is before.registry
    assert retained.registry.get("inspect_image")._broker is retained.vision_broker

    assert agent.config.set_field("vision_max_tokens", "2048")
    result = agent.apply_config_change("vision_max_tokens")
    rebound = agent._execution_dependencies

    assert result["applied"] is True
    assert rebound.vision_broker is not retained.vision_broker
    assert rebound.registry is not retained.registry
    assert rebound.registry.get("inspect_image")._broker is rebound.vision_broker


def test_rebuild_rejects_vision_change_for_injected_broker(tmp_path: Path) -> None:
    from pc_assistant.agent import Agent

    injected_broker = object()
    agent = Agent(
        config=_catalog_config(tmp_path, default_model="text"),
        vision_broker=injected_broker,
    )
    before = agent._execution_dependencies
    assert agent.config.set_field("vision_max_tokens", "2048")

    result = agent.apply_config_change("vision_max_tokens")

    assert result["applied"] is False
    assert agent._execution_dependencies is before
    assert agent.config.vision_max_tokens == before.config.vision_max_tokens
    assert before.vision_broker is injected_broker


def test_rebuild_rejects_identity_change_for_injected_vision_model(
    tmp_path: Path,
) -> None:
    from pc_assistant.agent import Agent

    agent = Agent(
        config=_catalog_config(tmp_path, default_model="text"),
        vision_llm=LLMProvider(supports_vision=True),
    )
    before = agent._execution_dependencies
    agent.config.vision_model = "text"

    result = agent.apply_config_change("vision_model")

    assert result["applied"] is False
    assert agent._execution_dependencies is before
    assert agent.config.vision_model == "vision"


def test_process_lifetime_trace_setting_requires_restart(tmp_path: Path) -> None:
    from pc_assistant.agent import Agent

    agent = Agent(config=_catalog_config(tmp_path))
    before = agent._execution_dependencies
    assert agent.config.set_field("trace_enabled", "false")

    result = agent.apply_config_change("trace_enabled")

    assert result["applied"] is False
    assert result["restart_required"] is True
    assert agent._execution_dependencies is before
    assert agent.config.trace_enabled is True


def test_reset_conversation_reuses_factory_system_prompt(tmp_path: Path) -> None:
    from pc_assistant.agent import Agent

    agent = Agent(config=_catalog_config(tmp_path))
    agent.conversation.add_user("discard me")

    agent.reset_conversation()

    messages = agent.conversation.get_messages_for_llm()
    assert messages[0]["role"] == "system"
    assert messages[0]["content"].startswith(agent._dependencies.system_prompt)


# covers agent-contracts-factory#REQ-004-S06
def test_vision_binding_validation_failure_publishes_nothing(tmp_path: Path) -> None:
    from pc_assistant.agent import Agent

    agent = Agent(config=_catalog_config(tmp_path, default_model="vision"))
    before = agent._execution_dependencies

    with patch.object(
        agent._factory,
        "_validate_execution_dependencies",
        side_effect=ValueError("schema mismatch"),
    ):
        result = agent.switch_model("text")

    assert result["applied"] is False
    assert agent._execution_dependencies is before
    assert agent.config.default_model == "vision"
    assert "inspect_image" not in agent.registry.list_tools()


# covers agent-contracts-factory#REQ-004-S05
@pytest.mark.asyncio
async def test_spanning_turn_keeps_one_execution_generation(tmp_path: Path) -> None:
    from pc_assistant.agent import Agent

    trace = LLMTraceRecorder(path=str(tmp_path / "logs" / "llm_calls.jsonl"))
    agent = Agent(
        config=_catalog_config(tmp_path, default_model="vision"),
        trace=trace,
    )
    before = agent._execution_dependencies
    started = asyncio.Event()
    release = asyncio.Event()
    captured: dict[str, Any] = {}

    async def paused_stream(*args: Any, **kwargs: Any):
        captured["tools"] = kwargs["tools"]
        captured["cache_control"] = kwargs["cache_control"]
        started.set()
        await release.wait()
        yield StreamChunk(delta_content="old generation", finish_reason="")
        yield StreamChunk(finish_reason="stop")

    before.llm.chat_stream = paused_stream

    async def collect() -> list[Any]:
        return [event async for event in agent.run("hello", session_id="spanning")]

    active_turn = asyncio.create_task(collect())
    await started.wait()
    result = agent.switch_model("text")
    after = agent._execution_dependencies
    release.set()
    events = await active_turn

    assert result["applied"] is True
    assert after is not before
    assert any(event.type == "final_answer" for event in events)
    assert all(item["function"]["name"] != "inspect_image" for item in captured["tools"])
    assert trace.recent(1)[0]["model"] == "vision"
    assert before.resolved_model.alias == "vision"
    assert before.config.default_model == "vision"
    assert before.registry.get("inspect_image") is None
    assert before.verifier._registry is before.registry
    assert before.executor._registry is before.registry
    assert after.resolved_model.alias == "text"
    assert after.config.default_model == "text"
    assert after.registry.get("inspect_image") is not None
    assert after.verifier._registry is after.registry
    assert after.executor._registry is after.registry
