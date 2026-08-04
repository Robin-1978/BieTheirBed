"""Neutral multimodal content blocks and provider-specific serialization.

Content is either a plain ``str`` (legacy) or a list of blocks:

    {"type": "text",  "text": "..."}
    {"type": "image", "image_url": "data:image/jpeg;base64,...", "media_type": "image/jpeg"}

Helpers convert the neutral list into what each provider expects at the wire
level. The legacy ``str`` form is always valid input and stays byte-identical.
"""
from __future__ import annotations

from typing import Any

# Content = str | list[ContentBlock]
# ContentBlock = ContentText | ContentImage  (dicts, JSON-friendly)

ContentBlock = dict[str, Any]
Content = str | list[ContentBlock]


def text_block(text: str) -> dict[str, Any]:
    return {"type": "text", "text": text}


def build_image_block(image_url: str, media_type: str = "image/jpeg") -> dict[str, Any]:
    """Build a neutral image block from a data URL."""
    return {"type": "image", "image_url": image_url, "media_type": media_type}


def is_block_content(content: Any) -> bool:
    return isinstance(content, list)


def split_content(content: Any) -> list[dict[str, Any]]:
    """Normalize content to a block list (``str`` -> single text block)."""
    if isinstance(content, list):
        return list(content)
    return [text_block(str(content or ""))]


def text_content(content: Any) -> str:
    """Concatenated text of any content (blocks or plain string)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            b.get("text", "")
            for b in content
            if isinstance(b, dict) and b.get("type") == "text"
        )
    return str(content or "")


def has_image(content: Any) -> bool:
    if not isinstance(content, list):
        return False
    return any(isinstance(b, dict) and b.get("type") == "image" for b in content)


def _data_url_parts(image_url: str) -> tuple[str, str]:
    """Split ``data:<media>;base64,<data>`` into ``(media_type, data)``."""
    if image_url.startswith("data:"):
        meta, _, b64 = image_url.partition(",")
        media_type = meta.split(";")[0].removeprefix("data:")
        if not media_type:
            media_type = "image/jpeg"
        return media_type, b64
    return "image/jpeg", image_url


# ── Provider serialization ─────────────────────────────────────────────

def to_openai_content(content: Any) -> Any:
    """Convert neutral content to OpenAI ``content`` field.

    ``str`` stays ``str``; blocks map text -> ``{"type":"text"}`` and image ->
    ``{"type":"image_url","image_url":{"url": ...}}``.
    """
    if isinstance(content, str) or content is None:
        return content
    if not isinstance(content, list):
        return str(content)
    out: list[dict[str, Any]] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        kind = block.get("type")
        if kind == "text":
            out.append({"type": "text", "text": block.get("text", "")})
        elif kind == "image":
            out.append({
                "type": "image_url",
                "image_url": {"url": block.get("image_url", "")},
            })
        else:
            out.append(block)
    return out


def to_anthropic_content(content: Any) -> Any:
    """Convert neutral content to Anthropic ``content`` field.

    Plain strings pass through unchanged; block lists are mapped into Anthropic
    content blocks (text / base64 image sources).
    """
    if content is None or isinstance(content, str):
        return content
    if not isinstance(content, list):
        return str(content)

    out: list[dict[str, Any]] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        kind = block.get("type")
        if kind == "text":
            out.append({"type": "text", "text": block.get("text", "")})
        elif kind == "image":
            media_type, b64 = _data_url_parts(block.get("image_url", ""))
            out.append({
                "type": "image",
                "source": {"type": "base64", "media_type": media_type, "data": b64},
            })
        else:
            out.append(block)
    return out if out else ""


def image_data_url_from_file(path: str) -> str | None:
    """Minimal file->data-URL helper used by tools (avoids PIL dependency)."""
    from pathlib import Path

    p = Path(path)
    if not p.exists():
        return None
    try:
        data = p.read_bytes()
    except OSError:
        return None
    import base64
    import mimetypes

    media = mimetypes.guess_type(p.name)[0] or "image/png"
    return f"data:{media};base64,{base64.b64encode(data).decode('ascii')}"