"""Grid overlay for the visual layer.

``screen.look`` draws a lettered/numbered coordinate grid (columns A.., rows
1..) over the screenshot so the model can reference cells like ``B4`` instead
of guessing raw pixels. These helpers compute the mapping in one place so the
agent-side coordinate math and the overlay drawing can never drift apart.
"""
from __future__ import annotations

import string
from typing import Any

DEFAULT_COLS = 10
DEFAULT_ROWS = 10


def grid_dimensions(
    cols: int | None = DEFAULT_COLS,
    rows: int | None = DEFAULT_ROWS,
) -> tuple[int, int]:
    """Clamp and return a sane (cols, rows). Missing/zero uses the default."""
    def _clamp(value: int | None, default: int) -> int:
        if value is None or value <= 0:
            return default
        return max(1, min(int(value), 26))

    return _clamp(cols, DEFAULT_COLS), _clamp(rows, DEFAULT_ROWS)


def cell_label(col: int, row: int) -> str:
    """Label for a 0-indexed cell: (0, 0) -> 'A1', (1, 2) -> 'B3'."""
    return f"{string.ascii_uppercase[col]}{row + 1}"


def cell_for_point(
    x: int,
    y: int,
    *,
    cols: int = DEFAULT_COLS,
    rows: int = DEFAULT_ROWS,
    width: int,
    height: int,
) -> str | None:
    """Return the cell label containing pixel (x, y), or None if outside."""
    cols, rows = grid_dimensions(cols, rows)
    if width <= 0 or height <= 0 or x < 0 or y < 0 or x >= width or y >= height:
        return None
    col = min(int(x * cols / width), cols - 1)
    row = min(int(y * rows / height), rows - 1)
    return cell_label(col, row)


def point_for_cell(
    cell: str,
    *,
    cols: int = DEFAULT_COLS,
    rows: int = DEFAULT_ROWS,
    width: int,
    height: int,
) -> tuple[int, int] | None:
    """Return the center pixel (x, y) of a cell label like ``B4``, or None."""
    cell = (cell or "").strip().upper()
    if not cell:
        return None
    col = ord(cell[0]) - ord("A")
    try:
        row = int(cell[1:]) - 1
    except ValueError:
        return None
    cols, rows = grid_dimensions(cols, rows)
    if not (0 <= col < cols and 0 <= row < rows):
        return None
    if width <= 0 or height <= 0:
        return None
    x = int((col + 0.5) * width / cols)
    y = int((row + 0.5) * height / rows)
    return x, y


def draw_grid(
    img: Any,
    *,
    cols: int = DEFAULT_COLS,
    rows: int = DEFAULT_ROWS,
    fill: tuple[int, int, int, int] = (255, 0, 0, 160),
) -> Any:
    """Draw a grid overlay on a copy of the PIL image. Returns the new image.

    Returns the original image unchanged if Pillow is unavailable or the grid
    is disabled, so callers never crash on the optional dependency.
    """
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        return img
    if img is None:
        return img
    cols, rows = grid_dimensions(cols, rows)
    w, h = img.size
    overlay = img.convert("RGBA")
    grid_layer = Image.new("RGBA", overlay.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(grid_layer)
    line_fill = fill
    font = None
    try:
        font = ImageFont.load_default(size=11)
    except Exception:
        try:
            font = ImageFont.load_default()
        except Exception:
            font = None

    for c in range(1, cols):
        x = round(c * w / cols)
        draw.line([(x, 0), (x, h)], fill=line_fill, width=1)
    for r in range(1, rows):
        y = round(r * h / rows)
        draw.line([(0, y), (w, y)], fill=line_fill, width=1)

    if font is not None:
        for c in range(cols):
            for r in range(rows):
                x = round((c + 0.5) * w / cols)
                y = round((r + 0.5) * h / rows)
                label = cell_label(c, r)
                bbox = draw.textbbox((0, 0), label, font=font)
                tw = bbox[2] - bbox[0]
                th = bbox[3] - bbox[1]
                lx = x - tw / 2
                ly = y - th / 2
                draw.rectangle([lx - 2, ly - 2, lx + tw + 2, ly + th + 2], fill=(0, 0, 0, 120))
                draw.text((lx, ly), label, font=font, fill=(255, 255, 255, 255))

    out = Image.alpha_composite(overlay, grid_layer)
    if img.mode != "RGBA":
        out = out.convert("RGB")
    return out
