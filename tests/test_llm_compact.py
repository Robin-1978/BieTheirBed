from __future__ import annotations

import pytest

from pc_assistant.context.llm_compact import (
    build_summary_request,
    compact_conversation_llm,
    llm_summarize_facts,
)


async def _ok_llm(messages):
    return "User asked to list files. Assistant called shell and listed 3 files."


async def _failing_llm(messages):
    raise RuntimeError("llm down")


def _messages(n: int) -> list[dict]:
    msgs: list[dict] = []
    for i in range(n):
        msgs.append({"role": "user", "content": f"question {i}"})
        msgs.append({"role": "assistant", "content": f"answer {i}"})
    return msgs


class TestBuildSummaryRequest:
    def test_contains_history(self):
        req = build_summary_request("history text")
        assert req[0]["role"] == "system"
        assert "history text" in req[1]["content"]


class TestLlmSummarizeFacts:
    @pytest.mark.asyncio
    async def test_success(self):
        result = await llm_summarize_facts("some history", _ok_llm)
        assert result and "shell" in result

    @pytest.mark.asyncio
    async def test_failure_returns_none(self):
        result = await llm_summarize_facts("some history", _failing_llm)
        assert result is None


class TestCompactConversationLlm:
    @pytest.mark.asyncio
    async def test_returns_none_when_no_llm(self):
        assert await compact_conversation_llm(_messages(10), keep_recent=4, llm_call=None) is None

    @pytest.mark.asyncio
    async def test_returns_none_when_short(self):
        assert await compact_conversation_llm(_messages(1), keep_recent=4, llm_call=_ok_llm) is None

    @pytest.mark.asyncio
    async def test_compacts_with_llm(self):
        result = await compact_conversation_llm(_messages(6), keep_recent=2, llm_call=_ok_llm)
        assert result is not None
        # summary pair + recent messages (2 user + 2 assistant)
        assert result[0]["role"] == "user"
        assert "compacted_history" in result[0]["content"]
        assert result[1]["role"] == "assistant"
        assert len(result) >= 4

    @pytest.mark.asyncio
    async def test_falls_back_on_llm_error(self):
        result = await compact_conversation_llm(_messages(6), keep_recent=2, llm_call=_failing_llm)
        assert result is None
