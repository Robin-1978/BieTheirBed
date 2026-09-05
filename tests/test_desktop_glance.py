from __future__ import annotations

import base64
import io
import time
from unittest.mock import patch
import pytest
from PIL import Image

import knoa_platform.vision.glance as glance
from knoa_platform.vision.glance import capture_desktop_glance


@pytest.fixture(autouse=True)
def reset_glance_cache():
    glance._cached_glance = None
    yield
    glance._cached_glance = None


def test_capture_desktop_glance_fallback():
    with patch("knoa_platform.vision.glance._capture_screen_image", return_value=None), \
         patch("knoa_platform.vision.glance._get_active_window_info", return_value=("Knoa Agent", "Test Window")):
        record = capture_desktop_glance(
            task_id="task-123",
            attempt_id="att-1",
            task_title="Build app",
            execution_phase="compiling",
            now=100.0,
        )

    assert record["taskId"] == "task-123"
    assert record["attemptId"] == "att-1"
    assert record["timestamp"] == 100000
    assert record["thumbnailBase64"] == ""
    assert record["windowTitle"] == "Test Window"
    assert record["activeApp"] == "Knoa Agent"


def test_capture_desktop_glance_with_image():
    dummy_img = Image.new("RGB", (1920, 1080), color=(255, 0, 0))

    with patch("knoa_platform.vision.glance._capture_screen_image", return_value=dummy_img), \
         patch("knoa_platform.vision.glance._get_active_window_info", return_value=("VS Code", "editor.ts")):
        record = capture_desktop_glance(
            task_id="task-456",
            attempt_id="att-2",
            task_title="Code edit",
            execution_phase="writing",
            now=200.0,
        )

    assert record["taskId"] == "task-456"
    assert record["activeApp"] == "VS Code"
    assert record["windowTitle"] == "editor.ts"
    assert record["thumbnailBase64"] != ""

    # Verify thumbnail is valid JPEG and exactly 320x180
    image_bytes = base64.b64decode(record["thumbnailBase64"])
    thumb = Image.open(io.BytesIO(image_bytes))
    assert thumb.size == (320, 180)
    assert thumb.format == "JPEG"


def test_capture_desktop_glance_cache():
    dummy_img = Image.new("RGB", (640, 360), color=(0, 255, 0))

    with patch("knoa_platform.vision.glance._capture_screen_image", return_value=dummy_img) as mock_capture, \
         patch("knoa_platform.vision.glance._get_active_window_info", return_value=("Terminal", "bash")):
        # First call at t=300.0
        rec1 = capture_desktop_glance(task_id="t1", now=300.0)
        assert mock_capture.call_count == 1

        # Second call at t=301.0 (within 2s TTL) should hit cache and NOT call capture again
        rec2 = capture_desktop_glance(task_id="t2", now=301.0)
        assert mock_capture.call_count == 1
        assert rec2["thumbnailBase64"] == rec1["thumbnailBase64"]
        assert rec2["timestamp"] == rec1["timestamp"]
