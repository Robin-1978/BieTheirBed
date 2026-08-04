"""Image preprocessing for the multimodal pipeline.

All helpers are optional-dependency aware: if Pillow / mss are unavailable the
loader returns ``None`` so callers can degrade gracefully instead of crashing.
"""
from __future__ import annotations

import base64
import math
from pathlib import Path
from typing import Any

from pc_assistant.model_adapter.content import build_image_block

IMAGE_MEDIA_PREFIX = "data:image"


def _pillow_loaded() -> Any | None:
    try:
        from PIL import Image  # noqa: F401
        return Image
    except ImportError:
        return None


def load_image(path: str | Path):
    """Load an image file into a ``PIL.Image`` (or ``None`` if unavailable)."""
    Image = _pillow_loaded()
    if Image is None:
        return None
    try:
        with Image.open(path) as img:
            return img.convert("RGB")
    except Exception:
        return None


def resize_image(img, max_side: int = 1280):
    """Scale an image so its long edge is <= ``max_side``, preserving aspect."""
    if img is None or max_side <= 0:
        return img
    w, h = img.size
    longest = max(w, h)
    if longest <= max_side:
        return img
    scale = max_side / longest
    new_size = (max(1, round(w * scale)), max(1, round(h * scale)))
    return img.resize(new_size)


def encode_jpeg(img, quality: int = 70) -> tuple[bytes, str] | None:
    """JPEG-encode a PIL image. Returns ``(bytes, media_type)`` or ``None``."""
    if img is None:
        return None
    import io

    buf = io.BytesIO()
    try:
        img.save(buf, format="JPEG", quality=quality)
    except Exception:
        return None
    return buf.getvalue(), "image/jpeg"


def encode_png(img) -> tuple[bytes, str] | None:
    """Losslessly PNG-encode a PIL image."""
    if img is None:
        return None
    import io

    buf = io.BytesIO()
    try:
        img.save(buf, format="PNG", optimize=True)
    except Exception:
        return None
    return buf.getvalue(), "image/png"


def to_data_url(data: bytes, media_type: str = "image/jpeg") -> str:
    b64 = base64.b64encode(data).decode("ascii")
    return f"{IMAGE_MEDIA_PREFIX}/{media_type.split('/')[-1]};base64,{b64}"


def image_block_from_file(
    path: str | Path,
    *,
    max_side: int = 1280,
    quality: int = 70,
) -> dict[str, Any] | None:
    """Load + preprocess an image file into a neutral ``ContentImage`` block."""
    img = load_image(path)
    if img is None:
        return None
    img = resize_image(img, max_side)
    encoded = encode_png(img)
    if encoded is None:
        return None
    data, media_type = encoded
    return build_image_block(to_data_url(data, media_type), media_type)


def estimate_image_tokens(width: int, height: int) -> int:
    """Rough per-image token estimate (OpenAI-style high-res tile heuristic).

    Each 112px tile costs ~170 tokens; a small fixed overhead accounts for the
    global low-res base. This lets ``truncate_messages`` budget images without
    dropping them like oversized text blobs.
    """
    if width <= 0 or height <= 0:
        return 0
    tiles = math.ceil(width / 112) * math.ceil(height / 112)
    return max(1, 85 + tiles * 170)


def capture_block(
    region: dict[str, Any] | None = None,
    *,
    max_side: int = 1280,
    quality: int = 70,
    grid: bool = False,
    grid_cols: int = 10,
    grid_rows: int = 10,
) -> dict[str, Any] | None:
    """Capture the screen (or a region) into a neutral ``ContentImage`` block.

    ``region``: ``{"x", "y", "width", "height"}`` in screen pixels (full screen
    when omitted). Returns ``None`` when mss/Pillow are unavailable so callers
    can degrade gracefully.
    """
    try:
        import mss
    except ImportError:
        return None
    Image = _pillow_loaded()
    if Image is None:
        return None
    try:
        with mss.MSS() as sct:
            if region:
                monitor = {
                    "left": int(region.get("x", 0)),
                    "top": int(region.get("y", 0)),
                    "width": max(1, int(region.get("width", 1))),
                    "height": max(1, int(region.get("height", 1))),
                }
            else:
                monitor = sct.monitors[0]
            shot = sct.grab(monitor)
            img = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
    except Exception:
        return None

    if grid:
        from pc_assistant.vision.grid import draw_grid

        img = draw_grid(img, cols=grid_cols, rows=grid_rows)

    img = resize_image(img, max_side)
    encoded = encode_png(img)
    if encoded is None:
        return None
    data, media_type = encoded
    block = build_image_block(to_data_url(data, media_type), media_type)
    block["width"] = img.size[0]
    block["height"] = img.size[1]
    block["source_width"] = shot.size.width
    block["source_height"] = shot.size.height
    return block
