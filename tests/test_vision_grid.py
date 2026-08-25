"""Tests for screenshot grid overlay helpers."""
from __future__ import annotations

from io import BytesIO

import pytest
from PIL import Image

from knoa_platform.vision.grid import crop_region, overlay_grid, parse_grid_region


def _blank_jpeg(width: int = 400, height: int = 300) -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (width, height), color=(240, 240, 240)).save(
        buffer,
        format="JPEG",
    )
    return buffer.getvalue()


def test_overlay_grid_returns_jpeg_bytes() -> None:
    result = overlay_grid(_blank_jpeg(), grid_size=4)
    assert isinstance(result, bytes)
    assert result.startswith(b"\xff\xd8")


def test_parse_grid_region_for_four_by_four() -> None:
    assert parse_grid_region("A1", grid_size=4) == (0, 0)
    assert parse_grid_region("D4", grid_size=4) == (3, 3)


def test_crop_region_matches_grid_cell() -> None:
    source = _blank_jpeg(400, 400)
    cropped = crop_region(source, "B2", grid_size=4)
    image = Image.open(BytesIO(cropped))
    assert image.size == (100, 100)


def test_parse_grid_region_rejects_out_of_range_labels() -> None:
    with pytest.raises(ValueError):
        parse_grid_region("E1", grid_size=4)
