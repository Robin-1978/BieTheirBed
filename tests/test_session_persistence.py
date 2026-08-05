from __future__ import annotations

from pathlib import Path

import pytest

from pc_assistant.agent import Agent
from pc_assistant.config import AppConfig
from pc_assistant.context.session_db import SessionTranscriptRepository
from pc_assistant.llm_provider import StreamChunk


def test_session_transcript_round_trip_and_delete(tmp_path: Path):
    repo = SessionTranscriptRepository(tmp_path / "assistant.db")
    messages = [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi"},
        {"role": "tool", "content": "[tool_result_omitted: prior tool result ok]"},
    ]

    repo.save("feishu:principal", messages)
    assert repo.load("feishu:principal") == messages

    repo.delete("feishu:principal")
    assert repo.load("feishu:principal") == []


@pytest.mark.asyncio
async def test_agent_restores_named_session_after_restart(tmp_path: Path):
    config = AppConfig(runtime_root=str(tmp_path))
    first = Agent(config=config)

    async def answer(*_args, **_kwargs):
        yield StreamChunk(delta_content="记得上海", finish_reason="")
        yield StreamChunk(finish_reason="stop")

    first._llm.chat_stream = answer
    async for _event in first.run("我住在上海", session_id="feishu:principal"):
        pass

    restarted = Agent(config=config)
    restored = restarted.session_messages("feishu:principal")
    assert [message["content"] for message in restored] == ["我住在上海", "记得上海"]

    restarted.drop_session("feishu:principal")
    after_clear = Agent(config=config)
    assert after_clear.session_messages("feishu:principal") == []
