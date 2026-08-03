from __future__ import annotations

from pc_assistant.context.token_estimate import (
    TokenEstimator,
    count_cjk,
    estimate_messages_tokens,
    estimate_text_tokens,
    normalize_family,
)


class TestNormalizeFamily:
    def test_unknown_defaults(self):
        assert normalize_family("") == "default"
        assert normalize_family("local") == "default"

    def test_known_families(self):
        assert normalize_family("openai") == "openai"
        assert normalize_family("anthropic") == "anthropic"
        assert normalize_family("llamacpp") == "llamacpp"
        assert normalize_family("gemini") == "gemini"

    def test_model_name_inference(self):
        assert normalize_family("", "qwen3:32b") == "qwen"
        assert normalize_family("", "deepseek-chat") == "deepseek"
        assert normalize_family("", "claude-sonnet") == "anthropic"
        assert normalize_family("", "gpt-4o") == "openai"
        assert normalize_family("", "llama3.1") == "llamacpp"

    def test_fuzzy_mapping(self):
        assert normalize_family("openai-compatible") == "openai"


class TestEstimateTextTokens:
    def test_empty(self):
        assert estimate_text_tokens("") == 0

    def test_ascii(self):
        result = estimate_text_tokens("a" * 40)
        assert result == 10

    def test_cjk(self):
        result = estimate_text_tokens("你好世界" * 5, family="llamacpp")
        assert result == 20

    def test_cjk_default_ratio(self):
        result = estimate_text_tokens("你好世界" * 5)
        assert result == 24

    def test_mixed(self):
        result = estimate_text_tokens("Hello你好world世界")
        assert result >= 4


class TestCountCjk:
    def test_count(self):
        assert count_cjk("abc你好def") == 2


class TestTokenEstimator:
    def test_messages_tokens(self):
        est = TokenEstimator("default")
        messages = [
            {"role": "user", "content": "hello world"},
            {"role": "assistant", "content": "ok"},
            {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "shell", "arguments": {"command": "ls"}}}]},
        ]
        total = est.messages_tokens(messages)
        assert total > 0

    def test_calibrate(self):
        est = TokenEstimator("default")
        before = est.text_tokens("a" * 100)
        est.calibrate(observed_tokens=50, text="a" * 100)
        assert est.sample_count() == 1
        # Observed 2 chars/token (denser than the default 4) nudges the ratio down,
        # so the same text is estimated to need MORE tokens.
        ratios = est.ratios()
        assert ratios["ascii_chars_per_token"] < 4.0
        after = est.text_tokens("a" * 100)
        assert after > before

    def test_estimate_messages_tokens_helper(self):
        msgs = [{"role": "user", "content": "你好世界"}]
        assert estimate_messages_tokens(msgs, family="default") > 0
