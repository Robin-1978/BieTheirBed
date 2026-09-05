from __future__ import annotations

from knoa_agent.context import ContextEngine
from knoa_agent_contracts import RuntimeTurnContext


def _turn(index: int, *, tool: bool = False):
    messages = [{"role": "user", "content": f"question-{index}-" + "x" * 180}]
    if tool:
        messages.extend(
            [
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": f"call-{index}",
                            "type": "function",
                            "function": {
                                "name": "inspect",
                                "arguments": '{"target":"build"}',
                            },
                        }
                    ],
                },
                {
                    "role": "tool",
                    "tool_call_id": f"call-{index}",
                    "content": '{"summary":"build inspected"}',
                },
            ]
        )
    messages.append(
        {"role": "assistant", "content": f"answer-{index}-" + "y" * 180}
    )
    return messages


def test_context_engine_pins_authorized_context_before_current_turn() -> None:
    engine = ContextEngine(context_window=4096, completion_reserve=512)
    history = [*_turn(1), {"role": "user", "content": "current question"}]

    prepared = engine.prepare(
        system_prompt="system",
        model_history=list(history),
        durable_history=list(history),
        tools=(),
        context=RuntimeTurnContext(
            core_memory=("preferred_language: zh",),
            relevant_memory=("preferred_editor: vim",),
            episodic_memory=("Previously inspected the build",),
            skill_instructions="<active_skills>diagnose</active_skills>",
        ),
    )

    assert prepared.messages[0] == {"role": "system", "content": "system"}
    assert prepared.messages[-1]["content"] == "current question"
    runtime = prepared.messages[-2]["content"]
    assert "<core>" in runtime
    assert "preferred_language: zh" in runtime
    assert "preferred_editor: vim" in runtime
    assert "Previously inspected the build" in runtime
    assert "<active_skills>diagnose</active_skills>" in runtime
    assert prepared.messages[1 : len(_turn(1)) + 1] == tuple(_turn(1))


def test_context_engine_compacts_complete_turns_and_persists_summary() -> None:
    engine = ContextEngine(context_window=700, completion_reserve=350)
    history = [
        *_turn(0, tool=True),
        *_turn(1),
        *_turn(2),
        {"role": "user", "content": "current"},
    ]

    prepared = engine.prepare(
        system_prompt="system",
        model_history=list(history),
        durable_history=list(history),
        tools=(),
        context=RuntimeTurnContext(),
    )

    assert prepared.compacted is True
    assert prepared.covered_messages > 0
    assert "question-0" in prepared.summary
    assert "Tool: inspect" in prepared.summary
    assert prepared.tokens_after < prepared.tokens_before
    remaining = list(prepared.durable_history)
    tool_ids = {
        str(call["id"])
        for message in remaining
        for call in message.get("tool_calls", ())
    }
    assert all(
        message.get("role") != "tool"
        or str(message.get("tool_call_id")) in tool_ids
        for message in remaining
    )
    assert remaining[-1] == {"role": "user", "content": "current"}


def test_context_engine_sliding_tool_window_in_same_turn() -> None:
    engine = ContextEngine(context_window=600, completion_reserve=150)
    history = [
        {"role": "user", "content": "Search multiple topics"},
        {
            "role": "assistant",
            "content": "Searching step 1",
            "tool_calls": [{"id": "call-1", "type": "function", "function": {"name": "web_search", "arguments": "{}"}}],
        },
        {"role": "tool", "tool_call_id": "call-1", "content": "Large search result 1: " + "a" * 800},
        {
            "role": "assistant",
            "content": "Searching step 2",
            "tool_calls": [{"id": "call-2", "type": "function", "function": {"name": "web_search", "arguments": "{}"}}],
        },
        {"role": "tool", "tool_call_id": "call-2", "content": "Large search result 2: " + "b" * 800},
    ]

    prepared = engine.prepare(
        system_prompt="system",
        model_history=list(history),
        durable_history=list(history),
        tools=(),
        context=RuntimeTurnContext(),
    )

    # First tool result should be trimmed aggressively
    tool_msgs = [m for m in prepared.messages if m.get("role") == "tool"]
    assert len(tool_msgs) == 2
    assert "trimmed" in tool_msgs[0]["content"]
    assert len(tool_msgs[0]["content"]) < 400
    # Context should fit without throwing ContextBudgetExceeded
    assert prepared.tokens_after <= 1000

