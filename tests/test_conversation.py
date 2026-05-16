from __future__ import annotations

import pytest

from pc_assistant.context.conversation import ConversationManager


class TestConversationManager:
    def test_add_user(self):
        cm = ConversationManager()
        cm.set_system_context("You are helpful.")
        msg = cm.add_user("Hello")
        assert msg.role == "user"
        assert msg.content == "Hello"

    def test_add_assistant(self):
        cm = ConversationManager()
        cm.set_system_context("You are helpful.")
        msg = cm.add_assistant("Hi there")
        assert msg.role == "assistant"

    def test_add_system_raises(self):
        cm = ConversationManager()
        with pytest.raises(ValueError, match="System messages"):
            cm.add("system", "test")

    def test_get_messages_for_llm(self):
        cm = ConversationManager()
        cm.set_system_context("Be helpful.")
        cm.add_user("hi")
        cm.add_assistant("hello")
        messages = cm.get_messages_for_llm()
        assert messages[0]["role"] == "system"
        assert len(messages) == 3

    def test_date_context_injected(self):
        cm = ConversationManager()
        cm.set_system_context("Be helpful.")
        cm.add_user("hi")
        messages = cm.get_messages_for_llm()
        system_content = messages[0]["content"]
        assert "Current date" in system_content

    def test_tool_result(self):
        cm = ConversationManager()
        cm.set_system_context("sys")
        cm.add_tool_result("tc1", "result text")
        messages = cm.get_messages_for_llm()
        tool_msgs = [m for m in messages if m["role"] == "tool"]
        assert len(tool_msgs) == 1
        assert tool_msgs[0]["tool_call_id"] == "tc1"

    def test_clear(self):
        cm = ConversationManager()
        cm.set_system_context("sys")
        cm.add_user("hi")
        cm.clear()
        assert len(cm) == 0

    def test_max_messages(self):
        cm = ConversationManager(max_messages=5)
        cm.set_system_context("sys")
        for i in range(10):
            cm.add_user(f"msg {i}")
        assert len(cm) == 10

    def test_estimate_token_count(self):
        cm = ConversationManager()
        cm.set_system_context("sys")
        cm.add_user("Hello world")
        count = cm.estimate_token_count()
        assert count > 0

    def test_orphan_tool_messages_filtered(self):
        cm = ConversationManager()
        cm.set_system_context("sys")
        cm.add_user("hello")
        cm.add_assistant("thinking...", tool_calls=[
            {"id": "tc1", "type": "function", "function": {"name": "shell", "arguments": {"command": "ls"}}}
        ])
        cm.add_tool_result("tc1", "file1.txt")
        cm.add_assistant_final("Here are the files.")
        cm.add_tool_result("orphan_tc", "orphan result")
        messages = cm.get_messages_for_llm()
        tool_msgs = [m for m in messages if m["role"] == "tool"]
        assert len(tool_msgs) == 1
        assert tool_msgs[0]["tool_call_id"] == "tc1"

    def test_assistant_with_tool_calls_included(self):
        cm = ConversationManager()
        cm.set_system_context("sys")
        cm.add_user("list files")
        cm.add_assistant("checking...", tool_calls=[
            {"id": "tc1", "type": "function", "function": {"name": "shell", "arguments": {"command": "ls"}}}
        ])
        cm.add_tool_result("tc1", "file1.txt")
        messages = cm.get_messages_for_llm()
        assistant_msgs = [m for m in messages if m["role"] == "assistant"]
        assert len(assistant_msgs) == 1
        assert "tool_calls" in assistant_msgs[0]

    def test_assistant_without_tool_calls_no_tool_field(self):
        cm = ConversationManager()
        cm.set_system_context("sys")
        cm.add_user("hello")
        cm.add_assistant_final("Hi there!")
        messages = cm.get_messages_for_llm()
        assistant_msgs = [m for m in messages if m["role"] == "assistant"]
        assert len(assistant_msgs) == 1
        assert "tool_calls" not in assistant_msgs[0]
