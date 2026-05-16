from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pc_assistant.agent import Agent, AgentEvent, _strip_think_tags
from pc_assistant.config import AppConfig
from pc_assistant.llm_provider import LLMResponse, StreamChunk
from pc_assistant.platform_ import get_default_dangerous_commands
from pc_assistant.tools.base import ToolBase
from pc_assistant.tools.registry import ToolRegistry
from typing import Any


class TestStripThinkTags:
    def test_no_tags(self):
        clean, thinking = _strip_think_tags("Hello world")
        assert clean == "Hello world"
        assert thinking == ""

    def test_with_tags(self):
        clean, thinking = _strip_think_tags("<think >Let me think</think >Hello world")
        assert "Hello world" in clean
        assert "Let me think" in thinking

    def test_only_think_tag(self):
        clean, thinking = _strip_think_tags("<think >Just thinking</think >")
        assert clean == ""
        assert "Just thinking" in thinking

    def test_unclosed_think_tag(self):
        clean, thinking = _strip_think_tags("<think >Just thinking")
        assert clean == ""
        assert "Just thinking" in thinking

    def test_multiple_think_tags(self):
        text = "<think >step1</think > text1 <think >step2</think > text2"
        clean, thinking = _strip_think_tags(text)
        assert "text1" in clean
        assert "text2" in clean
        assert "step1" in thinking
        assert "step2" in thinking


class TestAgentEvent:
    def test_defaults(self):
        e = AgentEvent(type="thought")
        assert e.type == "thought"
        assert e.content == ""
        assert e.tool_name == ""
        assert e.tool_args == {}
        assert e.tool_result is None
        assert e.blocked is False
        assert e.iteration == 0

    def test_with_values(self):
        e = AgentEvent(
            type="tool_call",
            content="calling tool",
            tool_name="filesystem",
            tool_args={"action": "read"},
            tool_result={"content": "file data"},
            blocked=False,
            iteration=3,
        )
        assert e.type == "tool_call"
        assert e.tool_name == "filesystem"
        assert e.iteration == 3


class TestAgentInit:
    def test_default_init(self):
        agent = Agent(config=AppConfig())
        assert agent.conversation is not None
        assert agent.registry is not None

    def test_builtin_tools_registered(self):
        agent = Agent(config=AppConfig())
        tools = agent.registry.list_tools()
        assert "filesystem" in tools
        assert "shell" in tools
        assert "application" in tools
        assert "web" in tools
        assert "system" in tools
        assert "clipboard" in tools

    def test_custom_tool_registration(self):
        class CustomTool(ToolBase):
            name = "custom"
            description = "A custom tool"

            async def execute(self, **kwargs: Any) -> Any:
                return {"result": "custom"}

            def schema(self) -> dict[str, Any]:
                return {"name": self.name, "description": self.description, "parameters": {"type": "object", "properties": {}}}

        agent = Agent(config=AppConfig())
        agent.register_tool(CustomTool())
        assert "custom" in agent.registry.list_tools()


async def _mock_stream(chunks: list[StreamChunk]):
    for chunk in chunks:
        yield chunk


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


class TestAgentRun:
    @pytest.mark.asyncio
    async def test_direct_answer(self):
        agent = Agent(config=AppConfig())
        agent._llm.chat_stream = _make_stream_mock(content="Hello!", finish_reason="stop")
        events = await _collect_events(agent, "hi")
        assert any(e.type == "final_answer" and "Hello!" in e.content for e in events)

    @pytest.mark.asyncio
    async def test_thought_then_answer(self):
        agent = Agent(config=AppConfig())
        agent._llm.chat_stream = _make_stream_mock(content="Let me think... Hello!", finish_reason="stop")
        events = await _collect_events(agent, "hi")
        final_events = [e for e in events if e.type == "final_answer"]
        assert len(final_events) >= 1

    @pytest.mark.asyncio
    async def test_tool_call_flow(self, tmp_path):
        agent = Agent(config=AppConfig())
        test_file = str(tmp_path / "test.txt")
        first_stream = _make_stream_mock(
            content="Let me write a file.",
            tool_calls=[{
                "id": "call_1",
                "type": "function",
                "function": {"name": "filesystem", "arguments": {"action": "write", "path": test_file, "content": "hello"}},
            }],
            finish_reason="tool_calls",
        )
        second_stream = _make_stream_mock(content="File written!", finish_reason="stop")
        call_count = 0
        original_chat_stream = agent._llm.chat_stream

        def mock_chat_stream(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return first_stream(*args, **kwargs)
            else:
                return second_stream(*args, **kwargs)

        agent._llm.chat_stream = mock_chat_stream
        events = await _collect_events(agent, "write a file")
        tool_calls = [e for e in events if e.type == "tool_call" and not e.blocked]
        tool_results = [e for e in events if e.type == "tool_result"]
        final = [e for e in events if e.type == "final_answer"]
        assert len(tool_calls) >= 1
        assert len(tool_results) >= 1
        assert len(final) >= 1

    @pytest.mark.asyncio
    async def test_safety_blocked(self):
        agent = Agent(config=AppConfig())
        dangerous_cmd = get_default_dangerous_commands()[0]
        agent._llm.chat_stream = _make_stream_mock(
            content="Deleting.",
            tool_calls=[{
                "id": "call_1",
                "type": "function",
                "function": {"name": "shell", "arguments": {"command": dangerous_cmd}},
            }],
            finish_reason="tool_calls",
        )
        events = await _collect_events(agent, "delete everything")
        blocked = [e for e in events if e.type == "tool_call" and e.blocked]
        assert len(blocked) >= 1

    @pytest.mark.asyncio
    async def test_confirm_callback_allows(self):
        agent = Agent(config=AppConfig(), confirm_callback=lambda n, a: True)
        dangerous_cmd = get_default_dangerous_commands()[0]
        first_stream = _make_stream_mock(
            content="Deleting.",
            tool_calls=[{
                "id": "call_1",
                "type": "function",
                "function": {"name": "shell", "arguments": {"command": dangerous_cmd}},
            }],
            finish_reason="tool_calls",
        )
        second_stream = _make_stream_mock(content="Done!", finish_reason="stop")
        call_count = 0

        def mock_chat_stream(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return first_stream(*args, **kwargs)
            else:
                return second_stream(*args, **kwargs)

        agent._llm.chat_stream = mock_chat_stream
        events = await _collect_events(agent, "delete temp")
        allowed_calls = [e for e in events if e.type == "tool_call" and not e.blocked]
        assert len(allowed_calls) >= 1

    @pytest.mark.asyncio
    async def test_confirm_callback_denies(self):
        agent = Agent(config=AppConfig(), confirm_callback=lambda n, a: False)
        dangerous_cmd = get_default_dangerous_commands()[0]
        first_stream = _make_stream_mock(
            content="Deleting.",
            tool_calls=[{
                "id": "call_1",
                "type": "function",
                "function": {"name": "shell", "arguments": {"command": dangerous_cmd}},
            }],
            finish_reason="tool_calls",
        )
        second_stream = _make_stream_mock(content="Blocked.", finish_reason="stop")
        call_count = 0

        def mock_chat_stream(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return first_stream(*args, **kwargs)
            else:
                return second_stream(*args, **kwargs)

        agent._llm.chat_stream = mock_chat_stream
        events = await _collect_events(agent, "delete temp")
        blocked = [e for e in events if e.type == "tool_call" and e.blocked]
        assert len(blocked) >= 1

    @pytest.mark.asyncio
    async def test_iteration_limit(self):
        agent = Agent(config=AppConfig(max_iterations=2))
        agent._llm.chat_stream = _make_stream_mock(
            content="Thinking...",
            tool_calls=[{
                "id": "call_1",
                "type": "function",
                "function": {"name": "filesystem", "arguments": {"action": "exists", "path": "."}},
            }],
            finish_reason="tool_calls",
        )
        events = await _collect_events(agent, "keep going")
        limit_events = [e for e in events if e.type == "iteration_limit"]
        assert len(limit_events) >= 1

    @pytest.mark.asyncio
    async def test_string_arguments(self):
        agent = Agent(config=AppConfig())
        first_stream = _make_stream_mock(
            content="Using tool.",
            tool_calls=[{
                "id": "call_1",
                "type": "function",
                "function": {"name": "filesystem", "arguments": '{"action": "exists", "path": "."}'},
            }],
            finish_reason="tool_calls",
        )
        second_stream = _make_stream_mock(content="Done!", finish_reason="stop")
        call_count = 0

        def mock_chat_stream(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return first_stream(*args, **kwargs)
            else:
                return second_stream(*args, **kwargs)

        agent._llm.chat_stream = mock_chat_stream
        events = await _collect_events(agent, "check file")
        tool_calls = [e for e in events if e.type == "tool_call" and not e.blocked]
        assert len(tool_calls) >= 1

    @pytest.mark.asyncio
    async def test_rate_limit(self):
        agent = Agent(config=AppConfig())
        from pc_assistant.harness.limiter import RateLimiter
        agent._limiter = RateLimiter(max_calls=1, window_seconds=60)
        agent._llm.chat_stream = _make_stream_mock(content="ok")
        await _collect_events(agent, "first")
        events = await _collect_events(agent, "second")
        assert any(e.type == "error" and "Rate limit" in e.content for e in events)

    @pytest.mark.asyncio
    async def test_stream_error(self):
        agent = Agent(config=AppConfig())
        agent._llm.chat_stream = _make_stream_mock(finish_reason="error")
        chunks = [StreamChunk(delta_content="Connection failed", finish_reason="error")]

        async def error_stream(*args, **kwargs):
            for c in chunks:
                yield c

        agent._llm.chat_stream = error_stream
        events = await _collect_events(agent, "hello")
        assert any(e.type == "error" for e in events)

    @pytest.mark.asyncio
    async def test_stream_exception(self):
        agent = Agent(config=AppConfig())

        async def bad_stream(*args, **kwargs):
            raise RuntimeError("stream broke")
            yield

        agent._llm.chat_stream = bad_stream
        events = await _collect_events(agent, "hello")
        assert any(e.type == "error" for e in events)

    @pytest.mark.asyncio
    async def test_think_tags_filtered(self):
        agent = Agent(config=AppConfig())

        async def think_stream(*args, **kwargs):
            yield StreamChunk(delta_content="<think >Let me reason about this</think > The answer is 42", finish_reason="")
            yield StreamChunk(finish_reason="stop")

        agent._llm.chat_stream = think_stream
        events = await _collect_events(agent, "what is the answer")
        final = [e for e in events if e.type == "final_answer"]
        assert len(final) >= 1
        assert "42" in final[0].content
        thoughts = [e for e in events if e.type == "thought"]
        assert len(thoughts) >= 1
        think_deltas = [e for e in events if e.type == "stream_think_delta"]
        assert len(think_deltas) >= 1
        think_starts = [e for e in events if e.type == "think_start"]
        assert len(think_starts) >= 1
        think_ends = [e for e in events if e.type == "think_end"]
        assert len(think_ends) >= 1

    @pytest.mark.asyncio
    async def test_think_only_response(self):
        agent = Agent(config=AppConfig())

        call_count = 0

        async def think_only_stream(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                yield StreamChunk(delta_content="<think >I need to think about this</think >", finish_reason="")
                yield StreamChunk(finish_reason="stop")
            else:
                yield StreamChunk(delta_content="Here is my answer: 42", finish_reason="")
                yield StreamChunk(finish_reason="stop")

        agent._llm.chat_stream = think_only_stream
        events = await _collect_events(agent, "what is the answer")
        final = [e for e in events if e.type == "final_answer"]
        assert len(final) >= 1

    @pytest.mark.asyncio
    async def test_think_only_no_answer_returns_content(self):
        agent = Agent(config=AppConfig(max_iterations=4))

        async def always_think_stream(*args, **kwargs):
            yield StreamChunk(delta_content="<think >Let me think deeply</think >", finish_reason="")
            yield StreamChunk(finish_reason="stop")

        agent._llm.chat_stream = always_think_stream
        events = await _collect_events(agent, "hard question")
        final = [e for e in events if e.type == "final_answer"]
        assert len(final) >= 1

    @pytest.mark.asyncio
    async def test_total_tool_call_limit(self):
        agent = Agent(config=AppConfig(max_iterations=20))

        async def always_tool_stream(*args, **kwargs):
            yield StreamChunk(
                delta_content="",
                delta_tool_calls=[{
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "filesystem", "arguments": {"action": "exists", "path": "."}},
                }],
                finish_reason="tool_calls",
            )

        agent._llm.chat_stream = always_tool_stream
        events = await _collect_events(agent, "keep checking")
        limit_events = [e for e in events if e.type == "iteration_limit"]
        assert len(limit_events) >= 1

    @pytest.mark.asyncio
    async def test_cancel(self):
        agent = Agent(config=AppConfig())

        async def slow_stream(*args, **kwargs):
            yield StreamChunk(delta_content="Hello", finish_reason="")
            yield StreamChunk(finish_reason="stop")

        agent._llm.chat_stream = slow_stream
        events: list[AgentEvent] = []
        async for event in agent.run("hi"):
            events.append(event)
            if event.type == "stream_start":
                agent.cancel()
        assert any(e.type == "cancelled" for e in events)

    @pytest.mark.asyncio
    async def test_tool_exception(self):
        agent = Agent(config=AppConfig())
        agent._registry.execute = AsyncMock(side_effect=RuntimeError("tool failed"))
        first_stream = _make_stream_mock(
            content="Using tool.",
            tool_calls=[{
                "id": "call_1",
                "type": "function",
                "function": {"name": "filesystem", "arguments": {"action": "read", "path": "test.txt"}},
            }],
            finish_reason="tool_calls",
        )
        second_stream = _make_stream_mock(content="Tool failed, let me try something else.", finish_reason="stop")
        call_count = 0

        def mock_chat_stream(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return first_stream(*args, **kwargs)
            else:
                return second_stream(*args, **kwargs)

        agent._llm.chat_stream = mock_chat_stream
        events = await _collect_events(agent, "read file")
        error_results = [e for e in events if e.type == "tool_result" and e.tool_result and isinstance(e.tool_result, dict) and "error" in e.tool_result]
        assert len(error_results) >= 1


class TestAgentRunSimple:
    @pytest.mark.asyncio
    async def test_final_answer(self):
        agent = Agent(config=AppConfig())
        agent._llm.chat_stream = _make_stream_mock(content="Hello!", finish_reason="stop")
        result = await agent.run_simple("hi")
        assert "Hello!" in result

    @pytest.mark.asyncio
    async def test_error(self):
        agent = Agent(config=AppConfig())

        async def error_stream(*args, **kwargs):
            yield StreamChunk(delta_content="Error occurred", finish_reason="error")

        agent._llm.chat_stream = error_stream
        result = await agent.run_simple("hi")
        assert "Error" in result

    @pytest.mark.asyncio
    async def test_iteration_limit(self):
        agent = Agent(config=AppConfig(max_iterations=1))
        agent._llm.chat_stream = _make_stream_mock(
            content="Thinking...",
            tool_calls=[{"id": "c1", "type": "function", "function": {"name": "filesystem", "arguments": {"action": "exists", "path": "."}}}],
            finish_reason="tool_calls",
        )
        result = await agent.run_simple("keep going")
        assert "Maximum iterations" in result


class TestAgentHealthCheck:
    @pytest.mark.asyncio
    async def test_health_check(self):
        agent = Agent(config=AppConfig())
        agent._llm.health_check = AsyncMock(return_value=True)
        result = await agent.health_check()
        assert result is True


class TestAgentReset:
    def test_reset_conversation(self):
        agent = Agent(config=AppConfig())
        agent._conversation.add_user("hello")
        agent.reset_conversation()
        msgs = agent.conversation.get_messages()
        assert len(msgs) == 0
        llm_msgs = agent.conversation.get_messages_for_llm()
        system_msgs = [m for m in llm_msgs if m["role"] == "system"]
        assert len(system_msgs) >= 1


class TestAgentMessageFormat:
    def test_llm_messages_have_exactly_one_system_at_head(self):
        agent = Agent(config=AppConfig())
        agent._conversation.add_user("hello")
        agent._conversation.add_assistant("hi")
        agent._conversation.add_user("what time is it?")
        msgs = agent._conversation.get_messages_for_llm()
        system_msgs = [m for m in msgs if m["role"] == "system"]
        assert len(system_msgs) == 1, f"Expected exactly 1 system message, got {len(system_msgs)}"
        assert msgs[0]["role"] == "system", "First message must be system"

    def test_conversation_history_has_no_system_messages(self):
        agent = Agent(config=AppConfig())
        agent._conversation.add_user("hello")
        agent._conversation.add_assistant("hi")
        msgs = agent._conversation.get_messages()
        system_msgs = [m for m in msgs if m["role"] == "system"]
        assert len(system_msgs) == 0

    def test_system_message_contains_date_context(self):
        agent = Agent(config=AppConfig())
        agent._conversation.add_user("hello")
        msgs = agent._conversation.get_messages_for_llm()
        system_content = msgs[0]["content"]
        assert "Current date:" in system_content
        assert "Current time:" in system_content


class TestAgentGetStatus:
    def test_initial_status(self):
        agent = Agent(config=AppConfig())
        status = agent.get_status()
        assert status["status"] == "ready"
        assert status["provider"] == "llamacpp"
        assert status["total_tokens"] == 0
        assert "filesystem" in status["tools"]

    @pytest.mark.asyncio
    async def test_token_counting(self):
        agent = Agent(config=AppConfig())

        async def stream_with_usage(*args, **kwargs):
            yield StreamChunk(delta_content="Hello!", finish_reason="")
            yield StreamChunk(finish_reason="stop", usage={"prompt_tokens": 50, "completion_tokens": 20, "total_tokens": 70})

        agent._llm.chat_stream = stream_with_usage
        await _collect_events(agent, "hi")
        status = agent.get_status()
        assert status["total_prompt_tokens"] == 50
        assert status["total_completion_tokens"] == 20
        assert status["total_tokens"] == 70
