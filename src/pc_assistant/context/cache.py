"""Prompt-cache planning — static prefix isolation for cache-capable backends.

For backends that support prompt caching (Anthropic, DeepSeek, some OpenAI
models, llama.cpp with ``cache_prompt``/``--cache-reuse``), a stable static
prefix (system prompt + tool schemas + runtime context) can be cached across
calls. `CachePlan` isolates that prefix and exposes a stable `prompt_cache_key`
so downstream providers can attach `cache_control` hints or reuse a KV-cache
slot.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from pc_assistant.context.token_estimate import TokenEstimator, estimate_messages_tokens

_CACHE_CAPABLE_PROVIDERS = {"anthropic", "deepseek", "openai", "gemini", "llamacpp"}
# llama.cpp exposes cache_prompt / --cache-reuse (KV prefix cache); vLLM / ollama
# expose their own KV cache and are treated as unknown.
_NON_CACHE_PROVIDERS = {"local"}


def _stable_digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


@dataclass
class CachePlan:
    """Computed static prefix for the current request."""

    provider: str
    model: str
    server_url: str
    static_prefix: str
    prefix_tokens: int
    supports_caching: bool
    context_id: str = ""
    prompt_cache_key: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "model": self.model,
            "server_url": self.server_url,
            "prefix_tokens": self.prefix_tokens,
            "supports_caching": self.supports_caching,
            "context_id": self.context_id,
            "prompt_cache_key": self.prompt_cache_key,
        }

    def cache_control_hint(self) -> dict[str, Any] | None:
        """Payload snippet providers may attach to the prefix message."""
        if not self.supports_caching:
            return None
        return {"cache_control": {"type": "ephemeral"}}


def build_static_prefix(system_prompt: str, tool_schemas: list[dict[str, Any]], extra: str = "") -> str:
    """Serialize the stable, reusable prefix of every request."""
    parts: list[str] = []
    if system_prompt:
        parts.append(f"<system>\n{system_prompt}\n</system>")
    if tool_schemas:
        parts.append(f"<tools>\n{json.dumps(tool_schemas, ensure_ascii=False)}\n</tools>")
    if extra:
        parts.append(f"<context>\n{extra}\n</context>")
    return "\n\n".join(parts)


def provider_supports_caching(provider: str) -> bool:
    key = (provider or "").strip().lower()
    if key in _NON_CACHE_PROVIDERS:
        return False
    if not key or key == "default":
        return False
    if key in _CACHE_CAPABLE_PROVIDERS:
        return True
    return key not in _NON_CACHE_PROVIDERS and "compat" not in key


def build_cache_plan(
    *,
    provider: str,
    model: str = "",
    server_url: str = "",
    system_prompt: str = "",
    tool_schemas: list[dict[str, Any]] | None = None,
    extra_static: str = "",
    estimator: TokenEstimator | None = None,
) -> CachePlan:
    prefix = build_static_prefix(system_prompt, tool_schemas or [], extra_static)
    if estimator is not None:
        prefix_tokens = estimator.text_tokens(prefix)
    else:
        prefix_tokens = estimate_messages_tokens(
            [{"role": "system", "content": prefix}],
            family=provider,
        )
    supports = provider_supports_caching(provider)
    return CachePlan(
        provider=provider,
        model=model,
        server_url=server_url,
        static_prefix=prefix,
        prefix_tokens=prefix_tokens,
        supports_caching=supports,
        context_id=_stable_digest(prefix),
        prompt_cache_key=f"{provider}:{model}:{_stable_digest(prefix)}",
    )
