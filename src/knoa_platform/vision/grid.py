"""Grid overlay helpers for visual grounding on screenshots."""
from __future__ import annotations

import re
from io import BytesIO

_REGION_PATTERN = re.compile(r"^([A-Z])([1-9][0-9]*)$")


def _column_labels(grid_size: int) -> str:
    if grid_size <= 0:
        raise ValueError("grid_size must be positive")
    if grid_size > 26:
        raise ValueError("grid_size must be at most 26")
    return "ABCDEFGHIJKLMNOPQRSTUVWXYZ"[:grid_size]


def parse_grid_region(region: str, *, grid_size: int = 4) -> tuple[int, int]:
    """Return zero-based (column, row) for a label such as ``A1`` or ``D4``."""
    match = _REGION_PATTERN.match(str(region or "").strip().upper())
    if match is None:
        raise ValueError(f"Invalid grid region '{region}'. Expected labels like A1..{_column_labels(grid_size)[grid_size - 1]}{grid_size}")
    column = _column_labels(grid_size).index(match.group(1))
    row = int(match.group(2)) - 1
    if row < 0 or row >= grid_size or column >= grid_size:
        raise ValueError(
            f"Grid region '{region}' is outside a {grid_size}x{grid_size} grid "
            f"(columns A-{_column_labels(grid_size)[grid_size - 1]}, rows 1-{grid_size})"
        )
    return column, row


def overlay_grid(image_bytes: bytes, grid_size: int = 4) -> bytes:
    """Draw a labeled grid over ``image_bytes`` and return JPEG bytes."""
    if grid_size <= 0:
        raise ValueError("grid_size must be positive")
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError as exc:
        raise RuntimeError("Pillow is required for grid overlays") from exc

    image = Image.open(BytesIO(image_bytes)).convert("RGB")
    width, height = image.size
    draw = ImageDraw.Draw(image)
    columns = _column_labels(grid_size)
    col_width = width / grid_size
    row_height = height / grid_size
    line_color = (100, 116, 139)
    label_fill = (37, 99, 235, 180)
    label_text = (255, 255, 255)
    try:
        font = ImageFont.load_default(size=max(12, int(min(col_width, row_height) * 0.12)))
    except TypeError:
        font = ImageFont.load_default()

    for index in range(1, grid_size):
        x = int(index * col_width)
        y = int(index * row_height)
        draw.line([(x, 0), (x, height)], fill=line_color, width=2)
        draw.line([(0, y), (width, y)], fill=line_color, width=2)

    for row in range(grid_size):
        for col in range(grid_size):
            label = f"{columns[col]}{row + 1}"
            x0 = int(col * col_width)
            y0 = int(row * row_height)
            text_bbox = draw.textbbox((0, 0), label, font=font)
            text_w = text_bbox[2] - text_bbox[0]
            text_h = text_bbox[3] - text_bbox[1]
            pad = 4
            box = (x0 + 4, y0 + 4, x0 + 4 + text_w + pad * 2, y0 + 4 + text_h + pad * 2)
            draw.rectangle(box, fill=label_fill)
            draw.text((box[0] + pad, box[1] + pad), label, fill=label_text, font=font)

    output = BytesIO()
    image.save(output, format="JPEG", quality=85, optimize=True)
    return output.getvalue()


def crop_region(image_bytes: bytes, region: str, *, grid_size: int = 4) -> bytes:
    """Crop ``image_bytes`` to one labeled grid cell and return JPEG bytes."""
    try:
        from PIL import Image
    except ImportError as exc:
        raise RuntimeError("Pillow is required for grid crops") from exc

    column, row = parse_grid_region(region, grid_size=grid_size)
    image = Image.open(BytesIO(image_bytes)).convert("RGB")
    width, height = image.size
    col_width = width / grid_size
    row_height = height / grid_size
    left = int(column * col_width)
    top = int(row * row_height)
    right = int((column + 1) * col_width) if column < grid_size - 1 else width
    bottom = int((row + 1) * row_height) if row < grid_size - 1 else height
    cropped = image.crop((left, top, right, bottom))
    output = BytesIO()
    cropped.save(output, format="JPEG", quality=85, optimize=True)
    return output.getvalue()
