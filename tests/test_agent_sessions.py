from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from pc_assistant.agent import Agent
from pc_assistant.config import AppConfig
from pc_assistant.llm_provider import LLMResponse, StreamChunk


def _answer_stream(content: str = "Hello!"):
    async def _fn(*args, **kwargs):
        yield StreamChunk(delta_content=content, finish_reason="")
        yield StreamChunk(finish_reason="stop")
    return _fn


async def _collect(agent: Agent, text: str, session_id: str = ""):
    events = []
    async for event in agent.run(text, session_id=session_id):
        events.append(event)
    return events


class TestSessionIsolation:
    @pytest.mark.asyncio
    async def test_two_sessions_isolated(self):
        agent = Agent(config=AppConfig())
        agent._llm.chat_stream = _answer_stream()
        await _collect(agent, "hello from a", session_id="a")
        await _collect(agent, "hello from b", session_id="b")

        conv_a = agent._session_manager.get("a", "sys").conversation
        conv_b = agent._session_manager.get("b", "sys").conversation
        text_a = " ".join(m["content"] for m in conv_a.get_messages())
        text_b = " ".join(m["content"] for m in conv_b.get_messages())
        assert "hello from a" in text_a
        assert "hello from b" not in text_a
        assert "hello from b" in text_b

    @pytest.mark.asyncio
    async def test_default_session_still_works(self):
        agent = Agent(config=AppConfig())
        agent._llm.chat_stream = _answer_stream()
        events = await _collect(agent, "hi")
        assert any(e.type == "final_answer" for e in events)
        assert len(agent.conversation.get_messages()) == 2


class TestRollback:
    @pytest.mark.asyncio
    async def test_rollback_on_stream_error(self):
        agent = Agent(config=AppConfig())

        async def error_stream(*args, **kwargs):
            yield StreamChunk(delta_content="boom", finish_reason="error")

        agent._llm.chat_stream = error_stream
        await _collect(agent, "do something")
        # User message + partial outputs rolled back.
        assert len(agent.conversation.get_messages()) == 0

    @pytest.mark.asyncio
    async def test_no_rollback_on_success(self):
        agent = Agent(config=AppConfig())
        agent._llm.chat_stream = _answer_stream("done!")
        await _collect(agent, "hi")
        assert len(agent.conversation.get_messages()) == 2

    @pytest.mark.asyncio
    async def test_rollback_cancelled(self):
        agent = Agent(config=AppConfig())

        async def stream(*args, **kwargs):
            yield StreamChunk(delta_content="x", finish_reason="")
            yield StreamChunk(finish_reason="stop")

        agent._llm.chat_stream = stream
        events = []
        async for event in agent.run("hi"):
            events.append(event)
            if event.type == "stream_start":
                agent.cancel()
        assert any(e.type == "cancelled" for e in events)
        assert len(agent.conversation.get_messages()) == 0


class TestEvidenceWarning:
    @pytest.mark.asyncio
    async def test_warning_when_no_tools(self):
        agent = Agent(config=AppConfig())
        agent._llm.chat_stream = _answer_stream("It's noon.")
        events = await _collect(agent, "what time is it?")
        assert any(e.type == "evidence_warning" for e in events)
        assert any(e.type == "final_answer" for e in events)

    @pytest.mark.asyncio
    async def test_no_warning_for_plain_query(self):
        agent = Agent(config=AppConfig())
        agent._llm.chat_stream = _answer_stream("Hello back!")
        events = await _collect(agent, "hello")
        assert not any(e.type == "evidence_warning" for e in events)


class TestSessionStatus:
    @pytest.mark.asyncio
    async def test_active_sessions_in_status(self):
        agent = Agent(config=AppConfig())
        agent._llm.chat_stream = _answer_stream()
        await _collect(agent, "hi", session_id="sess-x")
        status = agent.get_status()
        assert status["active_sessions"] == 1
        stats = agent.session_stats()
        assert any(s["session_id"] == "sess-x" for s in stats)

    def test_health_check(self):
        agent = Agent(config=AppConfig())
        agent._llm.health_check = AsyncMock(return_value=True)
        import asyncio
        assert asyncio.run(agent.health_check()) is True


class TestLlmCompaction:
    @pytest.mark.asyncio
    async def test_compacts_when_enabled(self):
        agent = Agent(config=AppConfig(llm_compact_enabled=True))
        agent._llm.chat = AsyncMock(
            return_value=LLMResponse(content="EARLIER: three turns summarized", finish_reason="stop")
        )
        agent._llm.chat_stream = _answer_stream("hello back")

        conv = agent.conversation
        for i in range(3):
            conv.add_user(f"q{i}")
            conv.add_assistant(f"a{i}")

        await _collect(agent, "q3")

        # Compaction rebuilds to summary + ack + recent pair (kept_recent=2),
        # then the new assistant answer is appended.
        assert len(conv.get_messages()) == 5
        agent._llm.chat.assert_called_once()

    @pytest.mark.asyncio
    async def test_disabled_keeps_history(self):
        agent = Agent(config=AppConfig(llm_compact_enabled=False))
        agent._llm.chat_stream = _answer_stream("hello back")

        conv = agent.conversation
        for i in range(3):
            conv.add_user(f"q{i}")
            conv.add_assistant(f"a{i}")

        await _collect(agent, "q3")
        assert len(conv.get_messages()) == 8
        assert not any(
            m["content"].startswith("<compacted_history") for m in conv.get_messages()
        )
