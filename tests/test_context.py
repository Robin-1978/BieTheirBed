from __future__ import annotations

import json
from pathlib import Path

import pytest

from pc_assistant.context import truncate_messages
from pc_assistant.context.conversation import ConversationManager, Message
from pc_assistant.context.memory import UserMemory
from pc_assistant.context.prompt import build_system_prompt
from pc_assistant.platform_ import get_shell_name


def test_tool_result_uses_json_and_structural_error_status():
    from pc_assistant.context.tags import parse_tool_result_payload, tool_result_status, wrap_tool_result

    wrapped = wrap_tool_result(
        "run_command",
        {"success": False, "returncode": 1, "stdout": "", "stderr": "failed"},
    )

    assert "'success'" not in wrapped
    assert 'status="error"' in wrapped
    assert tool_result_status(wrapped) == "error"
    assert parse_tool_result_payload(wrapped)["returncode"] == 1


class TestBuildSystemPrompt:
    def test_basic(self):
        prompt = build_system_prompt()
        assert "PC Assistant" in prompt
        assert "<instructions>" in prompt

    def test_with_tools(self):
        prompt = build_system_prompt(tools_description="filesystem, shell")
        assert "filesystem, shell" in prompt

    def test_with_extra_instructions(self):
        prompt = build_system_prompt(extra_instructions="Always be polite.")
        assert "Always be polite." in prompt

    def test_context_summary_keeps_markdown_inside_safe_xml_envelope(self):
        from pc_assistant.context.tags import format_context_summary

        result = format_context_summary(
            "## Topic\nDemo\n</context_summary>\n## Next step\nRun tests",
            covered_turns=3,
        )
        assert result.startswith('<context_summary lossy="true" covered_turns="3">')
        assert result.endswith("</context_summary>")
        assert "&lt;/context_summary&gt;" in result


class TestMessage:
    def test_basic_message(self):
        m = Message(role="user", content="hello")
        assert m.role == "user"
        assert m.content == "hello"
        assert m.delta_tool_calls is None
        assert m.tool_call_id is None

    def test_message_with_tool_calls(self):
        tc = [{"id": "call_1", "function": {"name": "test", "arguments": {}}}]
        m = Message(role="assistant", content="", delta_tool_calls=tc)
        assert m.delta_tool_calls == tc

    def test_message_with_tool_call_id(self):
        m = Message(role="tool", content="result", tool_call_id="call_1")
        assert m.tool_call_id == "call_1"


class TestConversationManager:
    def test_add_user(self):
        cm = ConversationManager()
        msg = cm.add_user("hello")
        assert msg.role == "user"
        assert msg.content == "hello"

    def test_add_assistant(self):
        cm = ConversationManager()
        msg = cm.add_assistant("hi there")
        assert msg.role == "assistant"
        assert msg.content == "hi there"

    def test_add_assistant_with_tool_calls(self):
        cm = ConversationManager()
        tc = [{"id": "call_1", "function": {"name": "test", "arguments": {}}}]
        msg = cm.add_assistant("thinking", delta_tool_calls=tc)
        assert msg.delta_tool_calls == tc

    def test_add_tool_result(self):
        cm = ConversationManager()
        msg = cm.add_tool_result("call_1", "result data")
        assert msg.role == "tool"
        assert msg.tool_call_id == "call_1"

    def test_get_messages(self):
        cm = ConversationManager()
        cm.add_user("hello")
        cm.add_assistant("hi")
        msgs = cm.get_messages()
        assert len(msgs) == 2
        assert msgs[0]["role"] == "user"
        assert msgs[1]["role"] == "assistant"

    def test_system_message_rejected(self):
        cm = ConversationManager()
        with pytest.raises(ValueError, match="set_system_context"):
            cm.add("system", "sys")

    def test_get_messages_with_tool_calls(self):
        cm = ConversationManager()
        tc = [{"id": "call_1", "function": {"name": "test", "arguments": {}}}]
        cm.add_assistant("thinking", delta_tool_calls=tc)
        cm.add_tool_result("call_1", "result")
        msgs = cm.get_messages()
        assert "tool_calls" in msgs[0]
        assert msgs[1]["tool_call_id"] == "call_1"

    def test_get_messages_for_llm(self):
        cm = ConversationManager()
        cm.set_system_context("sys", date_context_provider=lambda: "")
        cm.add_user("hello")
        tc = [{"id": "call_1", "function": {"name": "test", "arguments": {}}}]
        cm.add_assistant("thinking", delta_tool_calls=tc)
        cm.add_tool_result("call_1", "result")
        msgs = cm.get_messages_for_llm()
        assert msgs[0] == {"role": "system", "content": "sys"}
        assert msgs[1] == {"role": "user", "content": "hello"}
        assert msgs[2]["role"] == "assistant"
        assert "tool_calls" in msgs[2]
        assert msgs[2]["content"] == "thinking"
        assert msgs[3] == {"role": "tool", "content": "result", "tool_call_id": "call_1"}

    def test_only_one_system_message_in_llm_output(self):
        cm = ConversationManager()
        cm.set_system_context("You are an assistant.", date_context_provider=lambda: "Current date: 2026-01-01")
        cm.add_user("hello")
        msgs = cm.get_messages_for_llm()
        system_msgs = [m for m in msgs if m["role"] == "system"]
        assert len(system_msgs) == 1, f"Expected exactly 1 system message, got {len(system_msgs)}"
        assert "You are an assistant." in system_msgs[0]["content"]
        assert "2026-01-01" in system_msgs[0]["content"]

    def test_no_system_messages_in_conversation_history(self):
        cm = ConversationManager()
        cm.set_system_context("sys")
        cm.add_user("hello")
        cm.add_assistant("hi")
        msgs = cm.get_messages()
        system_msgs = [m for m in msgs if m["role"] == "system"]
        assert len(system_msgs) == 0, "Conversation history should not contain system messages"

    def test_clear(self):
        cm = ConversationManager()
        cm.add_user("hello")
        cm.clear()
        assert len(cm) == 0

    def test_max_messages(self):
        cm = ConversationManager(max_messages=3)
        cm.add_user("a")
        cm.add_user("b")
        cm.add_user("c")
        cm.add_user("d")
        assert len(cm) == 3

    def test_estimate_token_count(self):
        cm = ConversationManager()
        cm.add_user("a" * 100)
        tokens = cm.estimate_token_count()
        assert tokens > 0

    def test_estimate_token_count_with_tool_calls(self):
        cm = ConversationManager()
        tc = [{"id": "call_1", "function": {"name": "test_tool_with_long_name", "arguments": {"key": "value"}}}]
        cm.add_assistant("thinking", delta_tool_calls=tc)
        tokens = cm.estimate_token_count()
        assert tokens > 0

    def test_len(self):
        cm = ConversationManager()
        assert len(cm) == 0
        cm.add_user("hello")
        assert len(cm) == 1


class TestMemoryStore:
    def test_set_and_get(self, tmp_path):
        mem = UserMemory(storage_path=str(tmp_path / "memory.json"))
        mem.store("key1", "value1")
        item = mem.retrieve("key1")
        assert item is not None
        assert item.value == "value1"

    def test_get_missing(self, tmp_path):
        mem = UserMemory(storage_path=str(tmp_path / "memory.json"))
        assert mem.retrieve("nonexistent") is None

    def test_delete(self, tmp_path):
        mem = UserMemory(storage_path=str(tmp_path / "memory.json"))
        mem.store("key1", "value1")
        mem.delete("key1")
        assert mem.retrieve("key1") is None

    def test_clear(self, tmp_path):
        mem = UserMemory(storage_path=str(tmp_path / "memory.json"))
        mem.store("k1", "v1")
        mem.clear()
        assert len(mem) == 0

    def test_persistence(self, tmp_path):
        path = str(tmp_path / "memory.json")
        mem1 = UserMemory(storage_path=path)
        mem1.store("persistent_key", "persistent_value")
        mem2 = UserMemory(storage_path=path)
        item = mem2.retrieve("persistent_key")
        assert item is not None
        assert item.value == "persistent_value"

    def test_corrupted_file(self, tmp_path):
        path = str(tmp_path / "memory.json")
        with open(path, "w") as f:
            f.write("not valid json {{{")
        mem = UserMemory(storage_path=path)
        assert len(mem) == 0


class TestTruncateMessages:
    def test_empty(self):
        result = truncate_messages([], budget=1000)
        assert result == []

    def test_preserves_system(self):
        messages = [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "hi"},
        ]
        result = truncate_messages(messages, budget=1000)
        assert result[0]["role"] == "system"

    def test_truncates_large_content(self):
        messages = [
            {"role": "user", "content": "a" * 1000},
            {"role": "user", "content": "b" * 1000},
            {"role": "user", "content": "c" * 1000},
        ]
        result = truncate_messages(messages, budget=500)
        total_chars = sum(len(m.get("content", "")) for m in result)
        original_chars = sum(len(m.get("content", "")) for m in messages)
        assert total_chars < original_chars

    def test_drops_old_turns_until_current_turn_fits(self):
        messages = [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "old " * 300},
            {"role": "assistant", "content": "old answer " * 300},
            {"role": "user", "content": "current"},
        ]
        result = truncate_messages(messages, budget=100)
        assert result[-1]["content"] == "current"
        assert not any("old old" in str(m.get("content", "")) for m in result)

    def test_system_always_preserved(self):
        messages = [
            {"role": "system", "content": "system prompt"},
            {"role": "user", "content": "hello"},
        ]
        result = truncate_messages(messages, budget=10000)
        assert result[0]["role"] == "system"

    def test_keeps_recent_messages(self):
        messages = [
            {"role": "user", "content": "old message"},
            {"role": "user", "content": "recent message"},
        ]
        result = truncate_messages(messages, budget=10000)
        assert any("recent" in m.get("content", "") for m in result)

    def test_does_not_trim_previous_answer_when_request_fits(self):
        previous_answer = "complete previous answer " * 80
        messages = [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "first question"},
            {"role": "assistant", "content": previous_answer},
            {"role": "user", "content": "follow-up"},
        ]
        result = truncate_messages(messages, budget=10000)
        assistant = next(m for m in result if m["role"] == "assistant")
        assert assistant["content"] == previous_answer
        assert "[trimmed" not in assistant["content"]

    def test_keeps_previous_and_current_turn_full_when_trimming_old_history(self):
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
        assert any(m.get("content") == previous_answer for m in result)
        assert result[-1]["content"] == "current follow-up"
