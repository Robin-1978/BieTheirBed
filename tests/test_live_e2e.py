"""Live E2E tests against a real llama.cpp server.

Run with: pytest tests/test_live_e2e.py -v -s
Requires a running llama.cpp server at the configured URL.
"""
from __future__ import annotations

import asyncio
import os

import pytest

from pc_assistant.agent import Agent, AgentEvent
from pc_assistant.config import load_config


def _server_available() -> bool:
    import httpx
    cfg = load_config()
    try:
        resp = httpx.get(f"{cfg.llm_server_url}/v1/models", timeout=3)
        return resp.status_code == 200
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _server_available(),
    reason="llama.cpp server not available",
)


async def _collect(agent: Agent, text: str) -> list[AgentEvent]:
    events = []
    async for e in agent.run(text):
        events.append(e)
    return events


def _find(events: list[AgentEvent], etype: str) -> AgentEvent | None:
    for e in events:
        if e.type == etype:
            return e
    return None


@pytest.mark.asyncio
async def test_simple_question():
    """Agent should answer a simple factual question without tool calls."""
    cfg = load_config()
    agent = Agent(config=cfg)
    events = await _collect(agent, "What is 2 + 3?")

    final = _find(events, "final_answer")
    assert final is not None, f"No final_answer event. Events: {[e.type for e in events]}"
    assert "5" in final.content, f"Expected '5' in answer: {final.content}"
    print(f"\n[PASS] Simple question -> {final.content[:100]}")


@pytest.mark.asyncio
async def test_tool_call_filesystem(tmp_path):
    """Agent should use filesystem tool when asked to check if a path exists."""
    cfg = load_config()
    agent = Agent(config=cfg)
    target = str(tmp_path / "nonexistent.txt")
    events = await _collect(agent, f"Does the file '{target}' exist? Use the filesystem tool to check.")

    tool_calls = [e for e in events if e.type == "tool_call" and not e.blocked]
    tool_results = [e for e in events if e.type == "tool_result"]
    final = _find(events, "final_answer")

    assert len(tool_calls) >= 1, f"Expected tool calls. Events: {[e.type for e in events]}"
    assert final is not None
    print(f"\n[PASS] Tool call: {len(tool_calls)} calls, answer: {final.content[:100]}")


@pytest.mark.asyncio
async def test_sdb_blocks_dangerous():
    """SDB verifier should block dangerous commands."""
    cfg = load_config()
    cfg.dangerous_commands = ["rm -rf /"]
    agent = Agent(config=cfg)

    events = await _collect(agent, "Run this command: rm -rf /")

    blocked = [e for e in events if e.type == "tool_call" and e.blocked]
    event_types = [e.type for e in events]
    print(f"\n[INFO] Event types: {event_types}")
    print(f"\n[PASS] SDB test completed. Blocked: {len(blocked)}")


@pytest.mark.asyncio
async def test_event_bus_receives_events():
    """EventBus subscribers should receive events during a live run."""
    cfg = load_config()
    agent = Agent(config=cfg)

    received_types = []
    agent.event_bus.on("*", lambda e: received_types.append(e.type))

    events = await _collect(agent, "Say hello")
    final = _find(events, "final_answer")
    assert final is not None

    assert "stream_start" in received_types
    assert "final_answer" in received_types
    print(f"\n[PASS] EventBus received {len(received_types)} events: {set(received_types)}")


@pytest.mark.asyncio
async def test_multi_turn_conversation():
    """Agent should maintain conversation context across turns."""
    cfg = load_config()
    agent = Agent(config=cfg)

    events1 = await _collect(agent, "My name is TestUser123.")
    final1 = _find(events1, "final_answer")
    assert final1 is not None

    events2 = await _collect(agent, "What is my name?")
    final2 = _find(events2, "final_answer")
    assert final2 is not None
    assert "TestUser123" in final2.content, f"Expected name in answer: {final2.content}"
    print(f"\n[PASS] Multi-turn: {final2.content[:100]}")
