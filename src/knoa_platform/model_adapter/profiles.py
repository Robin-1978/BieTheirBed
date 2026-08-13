from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class VisionCapabilities:
    enabled: bool = False
    canonical_roles: frozenset[str] = frozenset({"user", "tool"})
    mime_types: frozenset[str] = frozenset({"image/jpeg", "image/png", "image/webp"})
    max_images: int = 8
    max_image_bytes: int = 20 * 1024 * 1024
    max_pixels: int = 20_000_000
    transport: str = "data_url"

    def validate(self, messages: list[dict[str, Any]]) -> str:
        images = 0
        for message in messages:
            content = message.get("content")
            if not isinstance(content, list):
                continue
            for block in content:
                if not isinstance(block, dict) or block.get("type") != "image":
                    continue
                if not self.enabled:
                    return "The active provider does not support image input"
                role = str(message.get("role", ""))
                if role not in self.canonical_roles:
                    return f"Provider does not accept canonical image role: {role}"
                media_type = str(block.get("media_type", "image/jpeg"))
                if media_type not in self.mime_types:
                    return f"Unsupported image MIME type: {media_type}"
                width = int(block.get("width", 0) or 0)
                height = int(block.get("height", 0) or 0)
                if width > 0 and height > 0 and width * height > self.max_pixels:
                    return f"Image exceeds provider pixel limit: {width}x{height}"
                image_url = str(block.get("image_url", ""))
                encoded = image_url.split(",", 1)[1] if "," in image_url else image_url
                approx_bytes = len(encoded) * 3 // 4
                if approx_bytes > self.max_image_bytes:
                    return f"Image exceeds provider byte limit: {approx_bytes}"
                images += 1
        if images > self.max_images:
            return f"Too many images for provider: {images} > {self.max_images}"
        return ""


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
    vision: VisionCapabilities = field(default_factory=VisionCapabilities)

    @property
    def supports_vision(self) -> bool:
        return self.vision.enabled


def _vision(enabled: bool) -> VisionCapabilities:
    return VisionCapabilities(enabled=enabled)


def resolve_profile(
    provider: str,
    server_url: str = "http://127.0.0.1:8080",
    api_key: str = "",
    api_base: str = "",
    *,
    supports_vision: bool | None = None,
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
            vision=_vision(True if supports_vision is None else supports_vision),
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
            vision=_vision(True if supports_vision is None else supports_vision),
        )

    if provider == "openai_compatible":
        base = (api_base or server_url).rstrip("/")
        # Treat an explicit API base as authoritative. Some vendors expose
        # OpenAI-compatible routes under version paths other than /v1.
        if api_base:
            chat_url = f"{base}/chat/completions"
            health_url = f"{base}/models"
        else:
            chat_url = _openai_style_endpoint(base, "/chat/completions")
            health_url = _openai_style_endpoint(base, "/models")
        return ProviderProfile(
            name="openai_compatible",
            server_url=base,
            chat_url=chat_url,
            health_url=health_url,
            headers={"Authorization": f"Bearer {api_key}"} if api_key else {},
            stream_options=True,
            # OpenAI-compatible endpoints do not share a reliable capability
            # contract. Unknown must fail closed; configure true explicitly
            # or route images through the dedicated vision model.
            vision=_vision(bool(supports_vision)),
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
        vision=_vision(bool(supports_vision)),
    )
