from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from pc_assistant.agent import Agent
from pc_assistant.config import AppConfig
from pc_assistant.llm_provider import StreamChunk
from pc_assistant.__init__ import _run_benchmark


class TestBenchmarkMode:
    @pytest.mark.asyncio
    async def test_ask_basic_text_output(self, capsys):
        agent = Agent(config=AppConfig())

        async def simple_stream(*args, **kwargs):
            yield StreamChunk(delta_content="The answer is 42", finish_reason="")
            yield StreamChunk(finish_reason="stop")

        agent._llm.chat_stream = simple_stream
        rc = await _run_benchmark(agent, "what is the answer")
        captured = capsys.readouterr()
        assert rc == 0
        assert "Question: what is the answer" in captured.out
        assert "The answer is 42" in captured.out
        assert "Time:" in captured.out
        assert "Tokens:" in captured.out

    @pytest.mark.asyncio
    async def test_ask_json_output(self, capsys):
        agent = Agent(config=AppConfig())

        async def simple_stream(*args, **kwargs):
            yield StreamChunk(delta_content="42", finish_reason="")
            yield StreamChunk(finish_reason="stop")

        agent._llm.chat_stream = simple_stream
        rc = await _run_benchmark(agent, "what is 6*7", json_output=True)
        captured = capsys.readouterr()
        assert rc == 0
        result = json.loads(captured.out)
        assert result["question"] == "what is 6*7"
        assert result["answer"] == "42"
        assert result["error"] is None
        assert "elapsed_seconds" in result["metrics"]
        assert "prompt_tokens" in result["metrics"]
        assert "tool_calls" in result["metrics"]

    @pytest.mark.asyncio
    async def test_ask_error_handling(self, capsys):
        agent = Agent(config=AppConfig())

        async def error_stream(*args, **kwargs):
            yield StreamChunk(delta_content="LLM server timeout", finish_reason="error")

        agent._llm.chat_stream = error_stream
        rc = await _run_benchmark(agent, "any question")
        captured = capsys.readouterr()
        assert rc == 1
        assert "LLM server timeout" in captured.out

    @pytest.mark.asyncio
    async def test_ask_json_error_output(self, capsys):
        agent = Agent(config=AppConfig())

        async def error_stream(*args, **kwargs):
            yield StreamChunk(delta_content="timeout", finish_reason="error")

        agent._llm.chat_stream = error_stream
        rc = await _run_benchmark(agent, "q", json_output=True)
        captured = capsys.readouterr()
        assert rc == 1
        result = json.loads(captured.out)
        assert result["error"] is not None
        assert "timeout" in result["error"]

    @pytest.mark.asyncio
    async def test_ask_tool_call_counted(self, capsys):
        agent = Agent(config=AppConfig())

        async def tool_stream(*args, **kwargs):
            yield StreamChunk(
                delta_content="Let me check",
                delta_tool_calls=[{
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "weather", "arguments": '{"location":"Beijing"}'},
                }],
                finish_reason="",
            )
            yield StreamChunk(finish_reason="tool_calls")

        agent._llm.chat_stream = tool_stream

        async def tool_exec(*args, **kwargs):
            return {"temperature": 25}

        agent._registry._tools["weather"].execute = AsyncMock(side_effect=tool_exec)

        rc = await _run_benchmark(agent, "weather in Beijing", json_output=True)
        captured = capsys.readouterr()
        result = json.loads(captured.out)
        assert result["metrics"]["tool_calls"] >= 1

    @pytest.mark.asyncio
    async def test_ask_no_tools_clears_registry(self, capsys):
        agent = Agent(config=AppConfig())

        async def simple_stream(*args, **kwargs):
            yield StreamChunk(delta_content="Hello", finish_reason="")
            yield StreamChunk(finish_reason="stop")

        agent._llm.chat_stream = simple_stream
        rc = await _run_benchmark(agent, "hi", no_tools=True)
        captured = capsys.readouterr()
        assert rc == 0
        assert "Hello" in captured.out

    @pytest.mark.asyncio
    async def test_ask_iteration_limit(self, capsys):
        agent = Agent(config=AppConfig(max_iterations=1))

        async def always_tool_stream(*args, **kwargs):
            yield StreamChunk(
                delta_content="",
                delta_tool_calls=[{
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "weather", "arguments": '{"city":"X"}'},
                }],
                finish_reason="",
            )
            yield StreamChunk(finish_reason="tool_calls")

        agent._llm.chat_stream = always_tool_stream

        async def tool_exec(*args, **kwargs):
            return {"temp": 30}

        agent._registry._tools["weather"].execute = AsyncMock(side_effect=tool_exec)

        rc = await _run_benchmark(agent, "weather")
        captured = capsys.readouterr()
        assert rc == 1
        assert "Maximum iterations" in captured.out

    @pytest.mark.asyncio
    async def test_ask_metrics_structure(self, capsys):
        agent = Agent(config=AppConfig())

        async def simple_stream(*args, **kwargs):
            yield StreamChunk(delta_content="OK", finish_reason="")
            yield StreamChunk(finish_reason="stop", usage={"prompt_tokens": 10, "completion_tokens": 2})

        agent._llm.chat_stream = simple_stream
        rc = await _run_benchmark(agent, "test", json_output=True)
        captured = capsys.readouterr()
        assert rc == 0
        result = json.loads(captured.out)
        m = result["metrics"]
        assert isinstance(m["elapsed_seconds"], float)
        assert m["elapsed_seconds"] >= 0
        assert isinstance(m["iterations"], int)
        assert isinstance(m["tool_calls"], int)
        assert isinstance(m["model"], str)
        assert isinstance(m["provider"], str)
