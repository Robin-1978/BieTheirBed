from __future__ import annotations

from pc_assistant.branding import ASSISTANT_IDENTITY
from pc_assistant.context.prompt import (
    _DEFAULT_SYSTEM_TEMPLATE,
    _SYSTEM_TEMPLATE_PATH,
    build_system_prompt,
)


class TestExternalPrompt:
    def test_template_file_exists(self):
        assert _SYSTEM_TEMPLATE_PATH.exists()

    def test_loads_from_file(self):
        prompt = build_system_prompt()
        assert "<role>" in prompt
        assert ASSISTANT_IDENTITY in prompt
        assert "<instructions>" in prompt
        assert "Independent tools may be called together" in prompt
        assert "receive no intermediate feedback" in prompt
        assert "do not emit user-facing prose" in prompt
        assert "simple standard Markdown" in prompt

    def test_file_matches_default_fallback(self):
        text = _SYSTEM_TEMPLATE_PATH.read_text(encoding="utf-8")
        assert text.strip() == _DEFAULT_SYSTEM_TEMPLATE.strip()

    def test_tools_description_appended(self):
        prompt = build_system_prompt(tools_description="shell, filesystem")
        assert "<available_tools>" in prompt
        assert "shell, filesystem" in prompt

    def test_extra_instructions_appended(self):
        prompt = build_system_prompt(extra_instructions="Always be polite.")
        assert "Always be polite." in prompt
