"""Neutral multimodal content blocks and provider-specific serialization.

Text-only messages use ``str``; multimodal messages use a list of blocks:

    {"type": "text",  "text": "..."}
    {"type": "image", "image_url": "data:image/jpeg;base64,...", "media_type": "image/jpeg"}

Helpers convert the neutral representation into each provider's wire format.
"""
from __future__ import annotations

import base64
import binascii
import io
from typing import Any

from PIL import Image, ImageOps, UnidentifiedImageError


class ImageNormalizationError(ValueError):
    """Raised before a provider can receive an unsafe image payload."""


MAX_PROVIDER_IMAGE_EDGE = 1024
MAX_PROVIDER_IMAGE_SOURCE_BYTES = 32 * 1024 * 1024
MAX_PROVIDER_IMAGE_SOURCE_PIXELS = 64_000_000
MAX_PROVIDER_IMAGE_BYTES = 2 * 1024 * 1024

def text_block(text: str) -> dict[str, Any]:
    return {"type": "text", "text": text}


def build_image_block(image_url: str, media_type: str = "image/jpeg") -> dict[str, Any]:
    """Build a neutral image block from a data URL."""
    return {"type": "image", "image_url": image_url, "media_type": media_type}


def split_content(content: Any) -> list[dict[str, Any]]:
    """Normalize content to a block list (``str`` -> single text block)."""
    if isinstance(content, list):
        return list(content)
    return [text_block(str(content or ""))]


def _data_url_parts(image_url: str) -> tuple[str, str]:
    """Split ``data:<media>;base64,<data>`` into ``(media_type, data)``."""
    if image_url.startswith("data:"):
        meta, _, b64 = image_url.partition(",")
        media_type = meta.split(";")[0].removeprefix("data:")
        if not media_type:
            media_type = "image/jpeg"
        return media_type, b64
    return "image/jpeg", image_url


def normalize_image_messages(
    messages: list[dict[str, Any]],
    *,
    max_edge: int = MAX_PROVIDER_IMAGE_EDGE,
    max_source_bytes: int = MAX_PROVIDER_IMAGE_SOURCE_BYTES,
    max_source_pixels: int = MAX_PROVIDER_IMAGE_SOURCE_PIXELS,
    max_output_bytes: int = MAX_PROVIDER_IMAGE_BYTES,
) -> list[dict[str, Any]]:
    """Return provider-safe messages with every inline image bounded.

    Durable Artifacts may retain their original bytes. This function creates
    only the bounded derivative placed on the model wire, so an old client or
    another ingress path cannot feed a full phone photo directly to llama.cpp.
    """

    normalized: list[dict[str, Any]] = []
    for message in messages:
        copied = dict(message)
        content = copied.get("content")
        if not isinstance(content, list):
            normalized.append(copied)
            continue
        blocks: list[Any] = []
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "image":
                blocks.append(block)
                continue
            blocks.append(
                _normalize_image_block(
                    block,
                    max_edge=max_edge,
                    max_source_bytes=max_source_bytes,
                    max_source_pixels=max_source_pixels,
                    max_output_bytes=max_output_bytes,
                )
            )
        copied["content"] = blocks
        normalized.append(copied)
    return normalized


def _normalize_image_block(
    block: dict[str, Any],
    *,
    max_edge: int,
    max_source_bytes: int,
    max_source_pixels: int,
    max_output_bytes: int,
) -> dict[str, Any]:
    image_url = str(block.get("image_url", ""))
    if not image_url.startswith("data:") or ";base64," not in image_url:
        raise ImageNormalizationError("Image input must be an inline base64 data URL")
    _media_type, encoded = _data_url_parts(image_url)
    estimated_size = len(encoded) * 3 // 4
    if estimated_size <= 0 or estimated_size > max_source_bytes:
        raise ImageNormalizationError("Image source exceeds the safe byte limit")
    try:
        source = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ImageNormalizationError("Image input contains invalid base64") from exc
    if len(source) > max_source_bytes:
        raise ImageNormalizationError("Image source exceeds the safe byte limit")

    try:
        with Image.open(io.BytesIO(source)) as opened:
            width, height = opened.size
            if width <= 0 or height <= 0 or width * height > max_source_pixels:
                raise ImageNormalizationError("Image source exceeds the safe pixel limit")
            # JPEG draft decoding avoids materializing the full camera frame
            # before it is reduced on memory-constrained Windows Nodes.
            if str(opened.format or "").upper() in {"JPEG", "MPO"}:
                opened.draft("RGB", (max_edge, max_edge))
            image = ImageOps.exif_transpose(opened)
            image.thumbnail((max_edge, max_edge), Image.Resampling.LANCZOS)
            if image.mode != "RGB":
                if "A" in image.getbands():
                    background = Image.new("RGB", image.size, "white")
                    background.paste(image, mask=image.getchannel("A"))
                    image = background
                else:
                    image = image.convert("RGB")
            output = _encode_bounded_jpeg(image, max_output_bytes)
            result = dict(block)
            result.update(
                {
                    "image_url": "data:image/jpeg;base64,"
                    + base64.b64encode(output).decode("ascii"),
                    "media_type": "image/jpeg",
                    "width": int(image.width),
                    "height": int(image.height),
                }
            )
            return result
    except ImageNormalizationError:
        raise
    except (Image.DecompressionBombError, UnidentifiedImageError, OSError) as exc:
        raise ImageNormalizationError("Image input cannot be decoded safely") from exc


def _encode_bounded_jpeg(image: Image.Image, max_output_bytes: int) -> bytes:
    for quality in (82, 72, 62):
        output = io.BytesIO()
        image.save(output, format="JPEG", quality=quality, optimize=True)
        value = output.getvalue()
        if len(value) <= max_output_bytes:
            return value
    raise ImageNormalizationError("Normalized image exceeds the provider byte limit")


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
