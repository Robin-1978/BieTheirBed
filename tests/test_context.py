from __future__ import annotations

from pc_assistant.context.assembly import truncate_messages
from pc_assistant.context.prompt import build_system_prompt
from pc_assistant.context.tags import (
    parse_tool_result_payload,
    tool_result_status,
    wrap_tool_result,
)


def test_tool_result_uses_json_and_structural_error_status() -> None:
    wrapped = wrap_tool_result(
        "run_command",
        {"success": False, "returncode": 1, "stdout": "", "stderr": "failed"},
    )

    assert "'success'" not in wrapped
    assert 'status="error"' in wrapped
    assert tool_result_status(wrapped) == "error"
    assert parse_tool_result_payload(wrapped)["returncode"] == 1


def test_system_prompt_contains_canonical_instruction_envelope() -> None:
    prompt = build_system_prompt(
        tools_description="filesystem, shell",
        extra_instructions="Always be polite.",
    )

    assert "PC Assistant" in prompt
    assert "<instructions>" in prompt
    assert "filesystem, shell" in prompt
    assert "Always be polite." in prompt


def test_truncate_messages_preserves_system_and_current_turn() -> None:
    messages = [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "old " * 300},
        {"role": "assistant", "content": "old answer " * 300},
        {"role": "user", "content": "current"},
    ]

    result = truncate_messages(messages, budget=100)

    assert result[0]["role"] == "system"
    assert result[-1]["content"] == "current"
    assert not any("old old" in str(message.get("content", "")) for message in result)


def test_truncate_messages_keeps_complete_recent_exchange() -> None:
    previous_answer = "important previous answer " * 60
    messages = [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "old question"},
        {"role": "assistant", "content": "old answer " * 500},
        {"role": "user", "content": "previous question"},
        {"role": "assistant", "content": previous_answer},
        {"role": "user", "content": "current follow-up"},
    ]

    result = truncate_messages(messages, budget=900)

    assert any(message.get("content") == previous_answer for message in result)
    assert result[-1]["content"] == "current follow-up"


def test_truncate_messages_does_not_modify_history_when_it_fits() -> None:
    previous_answer = "complete previous answer " * 80
    messages = [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "first question"},
        {"role": "assistant", "content": previous_answer},
        {"role": "user", "content": "follow-up"},
    ]

    result = truncate_messages(messages, budget=10_000)

    assistant = next(message for message in result if message["role"] == "assistant")
    assert assistant["content"] == previous_answer
    assert "[trimmed" not in assistant["content"]
