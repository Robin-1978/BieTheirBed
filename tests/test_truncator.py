from __future__ import annotations

import pytest

from pc_assistant.context.truncator import _estimate_tokens, truncate_messages, _group_tool_pairs


class TestEstimateTokens:
    def test_empty_string(self):
        assert _estimate_tokens("") == 0

    def test_english_text(self):
        result = _estimate_tokens("Hello world this is a test")
        assert result >= 1

    def test_cjk_text(self):
        cjk = "你好世界测试"
        result = _estimate_tokens(cjk)
        assert result == 6

    def test_mixed_text(self):
        text = "Hello你好world世界"
        result = _estimate_tokens(text)
        assert result >= 4

    def test_long_text(self):
        text = "a" * 100
        result = _estimate_tokens(text)
        assert result == 25


class TestGroupToolPairs:
    def test_no_tool_calls(self):
        messages = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello"},
        ]
        groups = _group_tool_pairs(messages)
        assert len(groups) == 2

    def test_tool_call_with_result(self):
        messages = [
            {"role": "assistant", "content": "", "delta_tool_calls": [{"id": "tc1", "function": {"name": "shell", "arguments": {}}}]},
            {"role": "tool", "content": "result", "tool_call_id": "tc1"},
        ]
        groups = _group_tool_pairs(messages)
        assert len(groups) == 1
        assert len(groups[0]) == 2

    def test_mixed_messages(self):
        messages = [
            {"role": "user", "content": "run ls"},
            {"role": "assistant", "content": "", "delta_tool_calls": [{"id": "tc1", "function": {"name": "shell", "arguments": {"command": "ls"}}}]},
            {"role": "tool", "content": "file1.txt\nfile2.txt", "tool_call_id": "tc1"},
            {"role": "assistant", "content": "Here are the files."},
        ]
        groups = _group_tool_pairs(messages)
        assert len(groups) == 3


class TestTruncateMessages:
    def test_empty_messages(self):
        assert truncate_messages([]) == []

    def test_system_preserved(self):
        messages = [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "hi"},
        ]
        result = truncate_messages(messages, budget=1000)
        assert any(m["role"] == "system" for m in result)

    def test_truncation_removes_old(self):
        messages = [
            {"role": "system", "content": "sys"},
        ]
        for i in range(20):
            messages.append({"role": "user", "content": f"message {i} " * 50})
            messages.append({"role": "assistant", "content": f"reply {i} " * 50})
        result = truncate_messages(messages, budget=200)
        assert len(result) < len(messages)
        assert result[0]["role"] == "system"

    def test_tool_output_truncated(self):
        messages = [
            {"role": "system", "content": "sys"},
            {"role": "tool", "content": "x" * 5000, "tool_call_id": "tc1"},
        ]
        result = truncate_messages(messages, budget=10000, max_tool_output_chars=100)
        tool_msg = [m for m in result if m["role"] == "tool"][0]
        assert len(tool_msg["content"]) < 5000
