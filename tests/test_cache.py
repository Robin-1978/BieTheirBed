from __future__ import annotations

from pc_assistant.context.cache import (
    build_cache_plan,
    build_static_prefix,
    provider_supports_caching,
)


class TestBuildStaticPrefix:
    def test_system_only(self):
        prefix = build_static_prefix("sys", [])
        assert "<system>" in prefix
        assert "sys" in prefix

    def test_with_tools(self):
        tools = [{"type": "function", "function": {"name": "shell", "description": "x"}}]
        prefix = build_static_prefix("sys", tools)
        assert "shell" in prefix
        assert "<tools>" in prefix

    def test_extra(self):
        prefix = build_static_prefix("sys", [], extra="ctx")
        assert "ctx" in prefix


class TestProviderCaching:
    def test_capable_providers(self):
        assert provider_supports_caching("anthropic")
        assert provider_supports_caching("deepseek")
        assert provider_supports_caching("openai")
        assert provider_supports_caching("gemini")

    def test_non_capable(self):
        assert not provider_supports_caching("local")
        assert not provider_supports_caching("")
        assert not provider_supports_caching("openai-compatible")

    def test_llamacpp_capable(self):
        assert provider_supports_caching("llamacpp")


class TestBuildCachePlan:
    def test_basic(self):
        plan = build_cache_plan(
            provider="anthropic",
            model="claude",
            system_prompt="sys",
            tool_schemas=[{"function": {"name": "shell"}}],
        )
        assert plan.supports_caching is True
        assert plan.prompt_cache_key
        assert plan.context_id
        assert plan.prefix_tokens > 0

    def test_key_stable(self):
        p1 = build_cache_plan(provider="openai", model="m", system_prompt="sys")
        p2 = build_cache_plan(provider="openai", model="m", system_prompt="sys")
        assert p1.prompt_cache_key == p2.prompt_cache_key
        assert p1.context_id == p2.context_id

    def test_key_changes_with_prompt(self):
        p1 = build_cache_plan(provider="openai", model="m", system_prompt="sys")
        p2 = build_cache_plan(provider="openai", model="m", system_prompt="sys2")
        assert p1.prompt_cache_key != p2.prompt_cache_key

    def test_cache_control_hint(self):
        plan = build_cache_plan(provider="anthropic", model="m", system_prompt="sys")
        hint = plan.cache_control_hint()
        assert hint == {"cache_control": {"type": "ephemeral"}}

    def test_cache_control_hint_llamacpp(self):
        plan = build_cache_plan(provider="llamacpp", model="m", system_prompt="sys")
        hint = plan.cache_control_hint()
        assert hint == {"cache_control": {"type": "ephemeral"}}

    def test_no_hint_without_caching(self):
        plan = build_cache_plan(provider="local", model="m", system_prompt="sys")
        assert plan.cache_control_hint() is None

    def test_to_dict(self):
        plan = build_cache_plan(provider="anthropic", model="m", system_prompt="sys")
        d = plan.to_dict()
        assert d["supports_caching"] is True
        assert d["prompt_cache_key"]
