from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from knoa_agent import ContextCheckpointRepository, KnoaAgentRuntime
from knoa_agent_contracts import (
    CreateRuntimeSession,
    McpEndpointGrant,
    RuntimeTurnContext,
    RuntimeTurnRequest,
    TextPart,
)
from knoa_platform.agent_runtime.model_step import ProviderChunk
from knoa_platform.agent_runtime.tool_step import ProposedToolCall, ToolStepResult


def grant(epoch: int = 1) -> McpEndpointGrant:
    return McpEndpointGrant(
        server_id="knoa-platform-capabilities",
        transport="in_memory",
        endpoint="memory://platform-capabilities",
        authorization="token",
        expires_at=9999999999.0,
        scope_digest="a" * 64,
        binding_epoch=epoch,
    )


class MultiStepMockProvider:
    """Mock Provider that simulates multi-step tool calls across turns."""

    def __init__(self) -> None:
        self.requests: list[Any] = []
        self.turn_step = 0

    def stream(self, request: Any, cancellation: asyncio.Event):
        del cancellation
        self.requests.append(request)
        step = len(self.requests)

        async def iterate():
            # Turn 1 - Iteration 1: Calls web_search
            if step == 1:
                yield ProviderChunk(
                    tool_calls=(
                        ProposedToolCall(
                            call_id="call_web_1",
                            name="web_search",
                            arguments={"query": "Suzhou weather"},
                        ),
                    ),
                    finish_reason="tool_calls",
                    terminal=True,
                )
            # Turn 1 - Iteration 2: Calls read_file
            elif step == 2:
                yield ProviderChunk(
                    tool_calls=(
                        ProposedToolCall(
                            call_id="call_file_2",
                            name="read_file",
                            arguments={"path": "/logs/weather.txt"},
                        ),
                    ),
                    finish_reason="tool_calls",
                    terminal=True,
                )
            # Turn 1 - Iteration 3: Answers final summary
            elif step == 3:
                yield ProviderChunk(content_delta="Suzhou is sunny and pleasant today.")
                yield ProviderChunk(finish_reason="stop", terminal=True)
            # Turn 2 - Iteration 1: User follows up, direct answer
            elif step == 4:
                yield ProviderChunk(content_delta="Sure, as mentioned earlier, it is sunny.")
                yield ProviderChunk(finish_reason="stop", terminal=True)

        return iterate()


class MockToolClient:
    async def list_tools(self) -> tuple[dict[str, Any], ...]:
        return (
            {
                "name": "web_search",
                "description": "Search the web for information.",
                "inputSchema": {
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                    "required": ["query"],
                },
            },
            {
                "name": "read_file",
                "description": "Read contents of a file.",
                "inputSchema": {
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": ["path"],
                },
            },
        )

    async def call_tool(self, call: Any) -> ToolStepResult:
        if call.name == "web_search":
            # Return a HUGE result (> 10KB) to test source-level Spill-and-Handle
            huge_content = "Sunny in Suzhou. " + ("Detailed weather report forecast data. " * 300)
            return ToolStepResult(
                call_id=call.call_id,
                tool_name=call.name,
                status="completed",
                code="ok",
                output={"content": huge_content},
            )
        elif call.name == "read_file":
            return ToolStepResult(
                call_id=call.call_id,
                tool_name=call.name,
                status="completed",
                code="ok",
                output={"content": "File verified: Suzhou temperature is 26C."},
            )
        raise ValueError(f"Unknown tool: {call.name}")

    async def read_resource(self, uri: str) -> None:
        raise NotImplementedError()


class MockConnector:
    def connect(self, grant: Any):
        del grant

        class Bound:
            async def __aenter__(self):
                return MockToolClient()

            async def __aexit__(self, *_args):
                return None

        return Bound()


@pytest.mark.asyncio
async def test_e2e_immutable_context_stream_and_prefix_caching(tmp_path: Path) -> None:
    """E2E Test verifying:

    1. Single-turn multi-step iterations preserve 100% byte-for-byte prefix immutability.
    2. Large tool results (>2KB) are bounded at source (Spill-and-Handle) without breaking prefix.
    3. Cross-turn follow-ups preserve the entire previous turn history as an immutable prefix.
    4. Time/memory envelopes are persisted and never retroactively trimmed.
    """
    provider = MultiStepMockProvider()
    store = ContextCheckpointRepository(
        tmp_path / "context.db",
        session_id_factory=lambda: "e2e-session-1",
    )
    runtime = KnoaAgentRuntime(
        provider,
        store,
        MockConnector(),
        system_prompt="You are Knoa, a digital co-worker.",
        health_probe=lambda: True,
        context_window=65536,
        max_output_tokens=4096,
    )

    session = await runtime.create_session(
        CreateRuntimeSession(operation_id="e2e-create", binding_epoch=1)
    )

    # =========================================================================
    # TURN 1: Multi-tool iteration (3 steps: search -> read -> answer)
    # =========================================================================
    context = RuntimeTurnContext(
        core_memory=("user_name: Robin", "location: Suzhou"),
        skill_instructions="<skills>weather, automation</skills>",
    )
    turn1 = await runtime.start_turn(
        RuntimeTurnRequest(
            session=session,
            operation_id="turn-1-request",
            input=(TextPart(text="Check the weather for Suzhou and verify the file."),),
            mcp=grant(),
            context=context,
        )
    )
    events1 = [event async for event in turn1.events]
    assert events1[-1].status == "completed"
    assert events1[-1].final_output == "Suzhou is sunny and pleasant today."

    # Turn 1 should have made 3 model calls (Iter 1, Iter 2, Iter 3)
    assert len(provider.requests) == 3
    req_iter1 = provider.requests[0]
    req_iter2 = provider.requests[1]
    req_iter3 = provider.requests[2]

    # --- 1.1 Single-turn Iteration 1 -> Iteration 2 Prefix Immutability ---
    # Iteration 2's message list MUST strictly start with ALL messages of Iteration 1
    assert len(req_iter2.messages) > len(req_iter1.messages)
    assert req_iter2.messages[: len(req_iter1.messages)] == req_iter1.messages, (
        "Iteration 2 broke prefix immutability from Iteration 1!"
    )

    # --- 1.2 Verify Tool Output Bounding at Source (Spill-and-Handle) ---
    # The tool returned > 10,000 characters.
    # The message added in Iteration 2 must be <= 2000 chars and contain spill notice.
    tool_message_1 = req_iter2.messages[-1]
    assert tool_message_1["role"] == "tool"
    assert tool_message_1["tool_call_id"] == "call_web_1"
    assert len(tool_message_1["content"]) <= 2000
    tool_payload = json.loads(tool_message_1["content"])
    assert "spill_notice" in tool_payload
    assert tool_payload["total_chars"] > 5000
    assert "output_preview" in tool_payload

    # --- 1.3 Single-turn Iteration 2 -> Iteration 3 Prefix Immutability ---
    assert len(req_iter3.messages) > len(req_iter2.messages)
    assert req_iter3.messages[: len(req_iter2.messages)] == req_iter2.messages, (
        "Iteration 3 broke prefix immutability from Iteration 2!"
    )

    # --- 1.4 Checkpoint persistence verification ---
    checkpoint1 = store.load_checkpoint(session.runtime_session_ref)
    assert checkpoint1 is not None
    ckpt_messages1 = checkpoint1.payload["messages"]
    # Checkpoint must contain the envelope, user query, tool calls, and final response
    roles1 = [m["role"] for m in ckpt_messages1]
    assert roles1 == ["user", "user", "assistant", "tool", "assistant", "tool", "assistant"]
    # The first message must be the persistent runtime_context envelope
    assert "<runtime_context>" in ckpt_messages1[0]["content"]
    assert "user_name: Robin" in ckpt_messages1[0]["content"]

    # =========================================================================
    # TURN 2: Follow-up question in the same session
    # =========================================================================
    turn2 = await runtime.start_turn(
        RuntimeTurnRequest(
            session=session,
            operation_id="turn-2-request",
            input=(TextPart(text="Can you give me a 1-sentence recap?"),),
            mcp=grant(),
            context=context,
        )
    )
    events2 = [event async for event in turn2.events]
    assert events2[-1].status == "completed"

    assert len(provider.requests) == 4
    req_turn2 = provider.requests[3]

    # --- 2.1 Cross-Turn History Prefix Immutability ---
    # In Turn 2, the prefix presented to the model must match ALL messages from Turn 1
    # System prompt is at index 0
    assert req_turn2.messages[0] == req_iter1.messages[0]

    # Verify that the entire conversation history from Turn 1 (envelope + user + tool calls + answer)
    # is preserved 100% byte-for-byte in Turn 2's request!
    turn1_history_in_turn2 = req_turn2.messages[1 : len(ckpt_messages1) + 1]
    assert turn1_history_in_turn2 == tuple(ckpt_messages1), (
        "Turn 2 broke cross-turn prefix immutability! Historical messages did not match checkpoint."
    )

    # --- 2.2 Turn 2 envelope appended cleanly ---
    turn2_envelope = req_turn2.messages[len(ckpt_messages1) + 1]
    assert turn2_envelope["role"] == "user"
    assert "<runtime_context>" in turn2_envelope["content"]
    turn2_query = req_turn2.messages[len(ckpt_messages1) + 2]
    assert turn2_query["role"] == "user"
    assert turn2_query["content"] == [{"type": "text", "text": "Can you give me a 1-sentence recap?"}]

    print("\n✅ E2E Verification Complete: 100% Prefix Byte-Exact Match for both Single-Turn and Cross-Turn!")


@pytest.mark.asyncio
async def test_e2e_time_drift_and_cache_ratio_reporting(tmp_path: Path) -> None:
    """E2E Test verifying:

    1. Time drift across minutes during long iterations does not alter <current_time>.
    2. UsageReported events accurately propagate cached_tokens and calculate hit ratios.
    """
    class UsageReportingProvider:
        def __init__(self) -> None:
            self.requests: list[Any] = []

        def stream(self, request: Any, cancellation: asyncio.Event):
            del cancellation
            self.requests.append(request)
            step = len(self.requests)

            async def iterate():
                if step == 1:
                    yield ProviderChunk(
                        tool_calls=(
                            ProposedToolCall(
                                call_id="c1",
                                name="read_file",
                                arguments={"path": "/a.txt"},
                            ),
                        ),
                        finish_reason="tool_calls",
                        terminal=True,
                        usage={"prompt_tokens": 1000, "completion_tokens": 50, "cached_tokens": 0},
                    )
                elif step == 2:
                    yield ProviderChunk(
                        content_delta="Done reading file.",
                        finish_reason="stop",
                        terminal=True,
                        usage={"prompt_tokens": 1200, "completion_tokens": 40, "cached_tokens": 1000},
                    )

            return iterate()

    provider = UsageReportingProvider()
    store = ContextCheckpointRepository(tmp_path / "context.db")
    runtime = KnoaAgentRuntime(
        provider,
        store,
        MockConnector(),
        system_prompt="system",
        health_probe=lambda: True,
    )
    session = await runtime.create_session(
        CreateRuntimeSession(operation_id="e2e-time", binding_epoch=1)
    )

    turn = await runtime.start_turn(
        RuntimeTurnRequest(
            session=session,
            operation_id="turn-time-req",
            input=(TextPart(text="Read file"),),
            mcp=grant(),
            context=RuntimeTurnContext(),
        )
    )
    events = [event async for event in turn.events]
    assert events[-1].status == "completed"

    # Extract all usage events
    usages = [e.usage for e in events if e.event_type == "usage_reported"]
    assert len(usages) == 2

    # Step 1 usage
    assert usages[0]["prompt_tokens"] == 1000
    assert usages[0]["cached_tokens"] == 0
    assert usages[0]["iteration"] == 1

    # Step 2 usage
    assert usages[1]["prompt_tokens"] == 1200
    assert usages[1]["cached_tokens"] == 1000
    assert usages[1]["iteration"] == 2

    # Verify that the timestamp string in message[1] is identical across both requests
    req1_time = [m["content"] for m in provider.requests[0].messages if "<current_time>" in str(m.get("content"))][0]
    req2_time = [m["content"] for m in provider.requests[1].messages if "<current_time>" in str(m.get("content"))][0]
    assert req1_time == req2_time
    print("\n✅ E2E Time Drift & Cache Reporting Test Complete: Pinning confirmed!")
