"""E2E tests for the architecture overhaul features."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from pc_assistant.agent import Agent, AgentEvent
from pc_assistant.config import AppConfig
from pc_assistant.harness.idempotency import IdempotencyLog
from pc_assistant.harness.refusal import RefusalCode, Verdict
from pc_assistant.harness.verifier import Verifier
from pc_assistant.context.memory import EpisodicMemory, ProceduralMemory, UserMemory
from pc_assistant.llm_provider import StreamChunk
from pc_assistant.planner import AgentPlanner
from pc_assistant.reflection import ReflectionChecker
from pc_assistant.tools.base import ToolBase
from pc_assistant.tools.mcp_adapter import MCPTool
from pc_assistant.tools.registry import ToolRegistry
from pc_assistant.exceptions import ToolNotFoundError


async def _collect_events(agent: Agent, user_input: str) -> list[AgentEvent]:
    events: list[AgentEvent] = []
    async for event in agent.run(user_input):
        events.append(event)
    return events


def _make_stream_mock(content: str = "", tool_calls: list[dict] | None = None, finish_reason: str = "stop"):
    chunks = []
    if content:
        chunks.append(StreamChunk(delta_content=content, finish_reason=""))
    if tool_calls:
        chunks.append(StreamChunk(delta_tool_calls=tool_calls, finish_reason=""))
    chunks.append(StreamChunk(finish_reason=finish_reason))

    async def _stream_fn(*args, **kwargs):
        for chunk in chunks:
            yield chunk

    return _stream_fn


# ================================================================
# SDB Verifier
# ================================================================

class TestVerifier:
    @pytest.mark.asyncio
    async def test_verifier_accepts_safe_tool(self):
        from pc_assistant.harness.safety import SafetyChecker
        from pc_assistant.harness.audit import AuditLogger

        registry = ToolRegistry()

        class DummyTool(ToolBase):
            name = "dummy"
            description = "test"
            async def execute(self, **kw): return "ok"
            def schema(self): return {"name": "dummy", "parameters": {"type": "object", "properties": {}}}

        registry.register(DummyTool())
        verifier = Verifier(
            safety=SafetyChecker(),
            registry=registry,
            audit=AuditLogger(),
        )
        verdict = await verifier.verify("dummy", {})
        assert verdict.accepted

    @pytest.mark.asyncio
    async def test_verifier_rejects_unknown_tool(self):
        from pc_assistant.harness.safety import SafetyChecker
        from pc_assistant.harness.audit import AuditLogger

        verifier = Verifier(
            safety=SafetyChecker(),
            registry=ToolRegistry(),
            audit=AuditLogger(),
        )
        verdict = await verifier.verify("nonexistent", {})
        assert verdict.rejected
        assert verdict.code == RefusalCode.TOOL_NOT_FOUND

    @pytest.mark.asyncio
    async def test_verifier_rejects_dangerous_command(self):
        from pc_assistant.harness.safety import SafetyChecker
        from pc_assistant.harness.audit import AuditLogger

        registry = ToolRegistry()
        from pc_assistant.tools.shell import ShellTool
        registry.register(ShellTool())

        verifier = Verifier(
            safety=SafetyChecker(dangerous_commands=["rm -rf /"]),
            registry=registry,
            audit=AuditLogger(),
        )
        verdict = await verifier.verify("shell", {"command": "rm -rf /"})
        assert verdict.rejected
        assert verdict.code == RefusalCode.DANGEROUS_COMMAND

    @pytest.mark.asyncio
    async def test_verdict_structured_message(self):
        v = Verdict.reject(RefusalCode.PROTECTED_PATH, "path is protected", "use a different path")
        msg = v.to_structured_message()
        assert "REJECTED" in msg
        assert "protected_path" in msg
        assert "Suggestion" in msg


# ================================================================
# Idempotency
# ================================================================

class TestIdempotency:
    def test_make_key_deterministic(self):
        k1 = IdempotencyLog.make_key("run1", 1, "shell", {"command": "ls"})
        k2 = IdempotencyLog.make_key("run1", 1, "shell", {"command": "ls"})
        assert k1 == k2

    def test_make_key_differs_on_args(self):
        k1 = IdempotencyLog.make_key("run1", 1, "shell", {"command": "ls"})
        k2 = IdempotencyLog.make_key("run1", 1, "shell", {"command": "pwd"})
        assert k1 != k2

    def test_check_miss_then_hit(self, tmp_path):
        log = IdempotencyLog(storage_path=str(tmp_path / "idem.json"))
        from pc_assistant.harness.idempotency import _SENTINEL
        assert log.check("key1") is _SENTINEL
        log.record("key1", {"result": "ok"})
        assert log.check("key1") == {"result": "ok"}

    def test_clear(self, tmp_path):
        log = IdempotencyLog(storage_path=str(tmp_path / "idem.json"))
        log.record("key1", "value")
        log.clear()
        from pc_assistant.harness.idempotency import _SENTINEL
        assert log.check("key1") is _SENTINEL


# ================================================================
# Planner
# ================================================================

class TestPlanner:
    def test_should_plan_simple_rejected(self):
        assert not AgentPlanner.should_plan("open browser")
        assert not AgentPlanner.should_plan("查看天气")
        assert not AgentPlanner.should_plan("list files")
        assert not AgentPlanner.should_plan("hi")

    def test_should_plan_complex_accepted(self):
        assert AgentPlanner.should_plan(
            "First download the dataset, then process it, and finally upload the results to S3"
        )

    def test_should_plan_short_input_rejected(self):
        assert not AgentPlanner.should_plan("deploy the app")


# ================================================================
# Reflection
# ================================================================

class TestReflection:
    def test_should_reflect_simple_skipped(self):
        checker = ReflectionChecker(llm=None, threshold=7)
        assert not checker.should_reflect("hello", "Hi there!", tool_call_count=0)

    def test_should_reflect_multi_tool(self):
        checker = ReflectionChecker(llm=None, threshold=7)
        assert checker.should_reflect("complex task", "result", tool_call_count=3)

    def test_should_reflect_risky_input(self):
        checker = ReflectionChecker(llm=None, threshold=7)
        assert checker.should_reflect("delete all logs", "ok", tool_call_count=0)

    def test_should_reflect_long_answer(self):
        checker = ReflectionChecker(llm=None, threshold=7)
        assert checker.should_reflect("question", "x" * 400, tool_call_count=0)


# ================================================================
# Tiered Memory
# ================================================================

class TestTieredMemory:
    def test_episodic_store_and_recall(self, tmp_path):
        mem = EpisodicMemory(storage_path=str(tmp_path / "ep.json"))
        mem.store_episode("User asked about weather", session_id="s1")
        mem.store_episode("User edited a file", session_id="s1")
        episodes = mem.recall(limit=5)
        assert len(episodes) == 2

    def test_episodic_query_recall(self, tmp_path):
        mem = EpisodicMemory(storage_path=str(tmp_path / "ep.json"))
        mem.store_episode("installed python package")
        mem.store_episode("checked weather in beijing")
        results = mem.recall("weather", limit=5)
        assert any("weather" in ep["summary"] for ep in results)

    def test_episodic_context_string(self, tmp_path):
        mem = EpisodicMemory(storage_path=str(tmp_path / "ep.json"))
        mem.store_episode("did something")
        ctx = mem.build_context_string()
        assert "Session History" in ctx

    def test_procedural_empty(self, tmp_path):
        mem = ProceduralMemory(procedures_dir=str(tmp_path / "procs"))
        assert len(mem) == 0
        assert mem.build_context_string() == ""

    def test_procedural_loads_md(self, tmp_path):
        procs_dir = tmp_path / "procs"
        procs_dir.mkdir()
        (procs_dir / "always_backup.md").write_text("Always backup before delete.")
        mem = ProceduralMemory(procedures_dir=str(procs_dir))
        assert len(mem) == 1
        assert "always_backup" in mem.list_procedures()
        ctx = mem.build_context_string()
        assert "backup" in ctx

    def test_user_memory_auto_extract(self):
        mem = UserMemory.__new__(UserMemory)
        mem._items = {}
        mem._storage_path = None
        mem._logger = None
        extracted = mem.extract_from_text("I prefer dark mode for everything.")
        assert len(extracted) >= 1


# ================================================================
# Typed Exceptions
# ================================================================

class TestTypedExceptions:
    @pytest.mark.asyncio
    async def test_tool_not_found_is_key_error(self):
        registry = ToolRegistry()
        with pytest.raises(KeyError):
            await registry.execute("nope")
        with pytest.raises(ToolNotFoundError):
            await registry.execute("nope")

    def test_tool_not_found_has_tool_name(self):
        try:
            raise ToolNotFoundError("my_tool")
        except ToolNotFoundError as e:
            assert e.tool_name == "my_tool"


# ================================================================
# MCP Adapter
# ================================================================

class TestMCPAdapter:
    def test_mcp_tool_schema(self):
        tool = MCPTool(
            name="test_mcp",
            description="A test MCP tool",
            input_schema={"type": "object", "properties": {"q": {"type": "string"}}},
            server_url="http://localhost:9999",
        )
        s = tool.schema()
        assert s["name"] == "test_mcp"
        assert "q" in s["parameters"]["properties"]

    def test_is_side_effecting_default_false(self):
        from pc_assistant.tools.base import ToolBase
        tool = MCPTool(
            name="t",
            description="",
            input_schema={},
            server_url="http://x",
        )
        assert not tool.is_side_effecting


# ================================================================
# Full Agent E2E with new features
# ================================================================

class TestAgentNewFeaturesE2E:
    @pytest.mark.asyncio
    async def test_sdb_blocks_and_emits_structured_rejection(self):
        config = AppConfig(dangerous_commands=["format c:"])
        agent = Agent(config=config)

        first_stream = _make_stream_mock(
            tool_calls=[{
                "id": "call_1",
                "type": "function",
                "function": {"name": "shell", "arguments": {"command": "format c:"}},
            }],
            finish_reason="tool_calls",
        )
        second_stream = _make_stream_mock(content="Blocked.", finish_reason="stop")
        call_count = 0

        def mock_stream(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            return first_stream(*args, **kwargs) if call_count == 1 else second_stream(*args, **kwargs)

        agent._llm.chat_stream = mock_stream
        events = await _collect_events(agent, "format the disk")
        blocked = [e for e in events if e.type == "tool_call" and e.blocked]
        assert len(blocked) >= 1

    @pytest.mark.asyncio
    async def test_is_side_effecting_flag(self):
        config = AppConfig()
        agent = Agent(config=config)
        shell = agent.registry.get("shell")
        fs = agent.registry.get("filesystem")
        web = agent.registry.get("web")
        weather = agent.registry.get("weather")
        assert shell.is_side_effecting is True
        assert fs.is_side_effecting is True
        assert web.is_side_effecting is False
        assert weather.is_side_effecting is False
