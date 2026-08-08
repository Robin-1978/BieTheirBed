from __future__ import annotations

from pc_assistant.branding import ASSISTANT_IDENTITY
from pc_assistant.context.assembly import assemble_llm_messages, truncate_messages
from pc_assistant.context.prompt import (
    build_session_context,
    build_system_prompt,
)


def _system_prompt() -> str:
    return build_system_prompt()


def test_system_prompt_appears_exactly_once() -> None:
    messages = assemble_llm_messages(
        _system_prompt(),
        [],
        "hi",
        memory_context="<memory>A</memory>",
    )
    blob = "".join(str(message.get("content", "")) for message in messages)

    assert "<system_rules>" not in blob
    assert blob.count(f"{ASSISTANT_IDENTITY}, an intelligent agent") == 1


def test_memory_lives_in_tail_pin() -> None:
    history = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
        {"role": "user", "content": "again"},
    ]
    messages = assemble_llm_messages(
        _system_prompt(),
        history,
        "again",
        memory_context="<memory>B</memory>",
    )

    head = [str(message.get("content", "")) for message in messages[:3]]
    assert not any("<user_memory>" in content for content in head)
    assert "<user_memory>" in messages[-2]["content"]
    assert "<memory>B</memory>" in messages[-2]["content"]
    assert messages[-1]["content"] == "again"


def test_history_prefix_is_stable_across_memory_change() -> None:
    first = assemble_llm_messages(
        _system_prompt(),
        [],
        "hi",
        memory_context="<memory>A</memory>",
    )
    second = assemble_llm_messages(
        _system_prompt(),
        [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello"},
            {"role": "user", "content": "again"},
        ],
        "again",
        memory_context="<memory>B</memory>",
    )

    assert first[0] == second[0]
    assert second[1] == {"role": "user", "content": "hi"}
    assert second[2] == {"role": "assistant", "content": "hello"}


def test_truncate_keeps_runtime_context_before_current_turn() -> None:
    history = [
        item
        for index in range(6)
        for item in (
            {"role": "user", "content": f"q{index}"},
            {"role": "assistant", "content": f"a{index}"},
        )
    ] + [{"role": "user", "content": "final question"}]
    messages = assemble_llm_messages(
        _system_prompt(),
        history,
        "final question",
        memory_context="<memory>C</memory>",
    )
    truncated = truncate_messages(messages, budget=100_000)

    runtime_index = max(
        index
        for index, message in enumerate(truncated)
        if str(message.get("content", "")).startswith("<runtime_context>")
    )
    assert runtime_index == len(truncated) - 2
    assert truncated[-1]["content"] == "final question"


def test_runtime_context_orders_stable_fields_before_current_time() -> None:
    context = build_session_context(
        memory_context="preferred_language=zh",
        os_info="Linux test",
    )

    assert context.index("<os_info>") < context.index("<user_memory>")
    assert context.index("<user_memory>") < context.index("<current_time>")
    assert "<working_directory>" not in context


def test_evidence_instruction_is_attached_to_current_turn() -> None:
    messages = assemble_llm_messages(
        _system_prompt(),
        [],
        "check memory",
        turn_context="## Evidence requirement\nBase on tool results.",
    )

    assert "Evidence requirement" not in messages[0]["content"]
    assert "Evidence requirement" in messages[-1]["content"]
