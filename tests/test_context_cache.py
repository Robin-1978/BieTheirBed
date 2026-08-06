from __future__ import annotations

import json

import pytest

from pc_assistant.context.assembly import assemble_llm_messages, truncate_messages
from pc_assistant.context.conversation import ConversationManager
from pc_assistant.context.prompt import (
    build_runtime_context,
    build_session_context,
    build_system_prompt,
)
from pc_assistant.observability.trace import LLMTraceRecorder


def _sys() -> str:
    return build_system_prompt()


class TestCacheFriendlyLayout:
    def test_system_prompt_appears_exactly_once(self):
        msgs = assemble_llm_messages(
            _sys(), [], "hi", memory_context="<memory>A</memory>",
        )
        blob = "".join(m.get("content", "") for m in msgs)
        assert "<system_rules>" not in blob
        assert blob.count("PC Assistant, an intelligent AI agent") == 1

    def test_runtime_context_has_no_system_rules(self):
        rc = build_runtime_context(
            memory_context="<memory>M</memory>",
            system_prompt="DUPLICATE",
        )
        assert "system_rules" not in rc
        assert "DUPLICATE" not in rc

    def test_memory_lives_in_tail_pin(self):
        sys_prompt = _sys()
        cm = ConversationManager()
        cm.add_user("hi")
        cm.add_assistant_final("hello")
        cm.add_user("again")
        msgs = assemble_llm_messages(
            sys_prompt, cm.get_messages_for_llm_raw(), "again",
            memory_context="<memory>B</memory>",
        )
        head = [m.get("content", "") for m in msgs[:3]]
        assert not any("<user_memory>" in c for c in head)
        tail = msgs[-2]["content"]
        assert "<user_memory>" in tail
        assert "<memory>B</memory>" in tail
        assert "<session>" in tail

    def test_history_prefix_stable_across_memory_change(self):
        """System + answered history must be byte-identical even when memory changes."""
        sys_prompt = _sys()
        cm = ConversationManager()

        msgs1 = assemble_llm_messages(
            sys_prompt, cm.get_messages_for_llm_raw(), "hi",
            memory_context="<memory>A</memory>",
        )
        cm.add_user("hi")
        cm.add_assistant_final("hello")
        cm.add_user("again")

        msgs2 = assemble_llm_messages(
            sys_prompt, cm.get_messages_for_llm_raw(), "again",
            memory_context="<memory>B</memory>",
        )

        # System message identical across turns.
        assert msgs1[0] == msgs2[0]
        # Turn 1's exchange is preserved verbatim in turn 2's history.
        assert msgs2[1]["content"] == "hi"
        assert msgs2[2] == {"role": "assistant", "content": "hello"}
        # The current turn is the last message.
        assert msgs2[-1]["content"] == "again"

    def test_truncate_keeps_session_pin_at_tail(self):
        sys_prompt = _sys()
        cm = ConversationManager()
        for i in range(6):
            cm.add_user(f"q{i}")
            cm.add_assistant_final(f"a{i}")
        cm.add_user("final question")
        msgs = assemble_llm_messages(
            sys_prompt, cm.get_messages_for_llm_raw(), "final question",
            memory_context="<memory>C</memory>",
        )
        truncated = truncate_messages(msgs, budget=100000)
        assert truncated[0]["role"] == "system"
        session_idx = max(
            i for i, m in enumerate(truncated)
            if m.get("content", "").startswith("<runtime_context>")
        )
        assert session_idx == len(truncated) - 2
        assert truncated[-1]["content"] == "final question"

    def test_runtime_context_orders_stable_fields_before_current_time(self):
        context = build_session_context(
            memory_context="preferred_language=zh",
            os_info="Linux test",
        )
        os_pos = context.index("<os_info>")
        memory_pos = context.index("<user_memory>")
        time_pos = context.index("<current_time>")
        assert "<working_directory>" not in context
        assert os_pos < memory_pos < time_pos
        assert context.rindex("</current_time>") < context.rindex("</session>")


class TestTurnContextPlacement:
    def test_evidence_instruction_goes_to_current_turn_not_system(self):
        msgs = assemble_llm_messages(
            _sys(), [], "check memory",
            turn_context="## Evidence requirement\nBase on tool results.",
        )
        assert "Evidence requirement" not in msgs[0]["content"]
        assert "Evidence requirement" in msgs[-1]["content"]


class TestToolCallStandardFormat:
    def test_tool_call_arguments_are_json_strings(self):
        sys_prompt = _sys()
        cm = ConversationManager()
        cm.add_user("list files")
        tc = [{
            "id": "call_1",
            "type": "function",
            "function": {"name": "shell", "arguments": {"command": "ls", "cwd": "/tmp"}},
        }]
        cm.add_assistant("", tool_calls=tc)
        cm.add_tool_result("call_1", "ok", tool_name="shell")
        cm.add_user("now what")
        msgs = assemble_llm_messages(
            sys_prompt, cm.get_messages_for_llm_raw(), "now what",
        )
        for m in msgs:
            for call in m.get("tool_calls") or []:
                args = call["function"]["arguments"]
                assert isinstance(args, str), f"arguments must be a JSON string, got {type(args)}"
                parsed = json.loads(args)
                assert parsed.get("command") == "ls"


class TestTraceCachedTokens:
    def test_record_call_includes_cached_tokens(self):
        class RecordingRecorder(LLMTraceRecorder):
            def __init__(self):
                super().__init__(enabled=False)
                self.entries = []

            def record(self, entry):
                self.entries.append(entry)

        rec = RecordingRecorder()
        rec.record_call(
            session_id="s1", model="test", iteration=0,
            prompt_tokens=100, completion_tokens=10, cached_tokens=19,
        )
        assert rec.entries[0]["cached_tokens"] == 19
        assert rec.entries[0]["cache_hit_ratio"] == pytest.approx(0.19)
