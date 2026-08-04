from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


def _openai_style_endpoint(server_url: str, suffix: str) -> str:
    """Build an OpenAI-compatible endpoint, avoiding a duplicated ``/v1``
    when the configured base already carries the API version prefix."""
    base = server_url.rstrip("/")
    if base.endswith("/v1"):
        return f"{base}{suffix}"
    return f"{base}/v1{suffix}"


@dataclass(frozen=True)
class ProviderProfile:
    name: str
    server_url: str
    chat_url: str
    health_url: str
    headers: dict[str, str]
    anthropic_style: bool = False
    cache_prompt: bool = False
    stream_options: bool = False
    requires_api_key: bool = False


def resolve_profile(
    provider: str,
    server_url: str = "http://127.0.0.1:8080",
    api_key: str = "",
    api_base: str = "",
) -> ProviderProfile:
    if provider == "openai":
        base = "https://api.openai.com/v1"
        return ProviderProfile(
            name="openai",
            server_url=base,
            chat_url=f"{base}/chat/completions",
            health_url=f"{base}/models",
            headers={"Authorization": f"Bearer {api_key}"} if api_key else {},
            stream_options=True,
            requires_api_key=True,
        )

    if provider == "anthropic":
        base = "https://api.anthropic.com"
        return ProviderProfile(
            name="anthropic",
            server_url=base,
            chat_url=f"{base}/v1/messages",
            health_url=f"{base}/v1/models",
            headers={"x-api-key": api_key, "anthropic-version": "2023-06-01"} if api_key else {},
            anthropic_style=True,
            requires_api_key=True,
        )

    if provider == "openai_compatible":
        base = (api_base or server_url).rstrip("/")
        return ProviderProfile(
            name="openai_compatible",
            server_url=base,
            chat_url=_openai_style_endpoint(base, "/chat/completions"),
            health_url=_openai_style_endpoint(base, "/models"),
            headers={"Authorization": f"Bearer {api_key}"} if api_key else {},
            stream_options=True,
        )

    base = server_url.rstrip("/")
    return ProviderProfile(
        name="llamacpp",
        server_url=base,
        chat_url=_openai_style_endpoint(base, "/chat/completions"),
        health_url=_openai_style_endpoint(base, "/models"),
        headers={},
        cache_prompt=True,
        stream_options=True,
    )
