"""Desktop Glance pipeline for capturing downsampled desktop thumbnails and window context."""

from __future__ import annotations

import base64
import io
import logging
import time
from typing import Any

from PIL import Image

logger = logging.getLogger(__name__)

_GLANCE_CACHE_TTL_SECONDS = 2.0
_cached_glance: tuple[float, str, str, str] | None = None  # (timestamp, thumbnail_b64, active_app, window_title)


def _capture_screen_image() -> Image.Image | None:
    from knoa_platform.desktop_companion import (
        desktop_companion_required,
        invoke_desktop_companion,
    )

    if desktop_companion_required():
        result = invoke_desktop_companion("screenshot", {})
        raw_b64 = result.get("content_base64")
        if not raw_b64:
            return None
        image_bytes = base64.b64decode(str(raw_b64), validate=True)
        image = Image.open(io.BytesIO(image_bytes))
        image.load()
        return image

    import mss

    with mss.MSS() as sct:
        mon = next(
            (m for m in sct.monitors[1:] if m.get("is_primary")),
            sct.monitors[1] if len(sct.monitors) > 1 else sct.monitors[0],
        )
        shot = sct.grab(mon)
        return Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")


def _get_active_window_info(default_title: str = "") -> tuple[str, str]:
    from knoa_platform.desktop_companion import (
        desktop_companion_required,
        invoke_desktop_companion,
    )

    if desktop_companion_required():
        try:
            result = invoke_desktop_companion("windows", {"action": "active"})
            win = result.get("window") or {}
            active_app = str(win.get("app_name") or "Knoa Agent").strip()
            window_title = str(win.get("title") or default_title or "Desktop").strip()
            return active_app, window_title
        except Exception as exc:
            logger.debug("Desktop Companion active window query failed: %s", exc)
            return "Knoa Agent", default_title or "Desktop"

    try:
        import pywinctl

        win = pywinctl.getActiveWindow()
        if win is not None:
            title = str(getattr(win, "title", "") or "").strip()
            get_app = getattr(win, "getAppName", None)
            app = str(get_app() if callable(get_app) else "").strip()
            return app or "Desktop", title or default_title or "Desktop"
    except Exception as exc:
        logger.debug("Local pywinctl active window query failed: %s", exc)

    return "Knoa Agent", default_title or "Desktop"


def capture_desktop_glance(
    *,
    task_id: str = "",
    attempt_id: str = "",
    task_title: str = "",
    execution_phase: str = "",
    now: float | None = None,
) -> dict[str, Any]:
    """Capture downsampled desktop thumbnail and active window info for mobile Glance."""
    global _cached_glance

    current_time = time.time() if now is None else now
    default_title = execution_phase or task_title or "Desktop"

    if _cached_glance is not None:
        cached_ts, cached_b64, cached_app, cached_title = _cached_glance
        if 0 <= current_time - cached_ts < _GLANCE_CACHE_TTL_SECONDS:
            return {
                "taskId": task_id,
                "attemptId": attempt_id,
                "timestamp": int(cached_ts * 1000),
                "thumbnailBase64": cached_b64,
                "windowTitle": cached_title or default_title,
                "activeApp": cached_app or "Knoa Agent",
            }

    thumbnail_b64 = ""
    try:
        image = _capture_screen_image()
        if image is not None:
            thumb = image.resize((320, 180), Image.Resampling.LANCZOS)
            buffer = io.BytesIO()
            thumb.save(buffer, format="JPEG", quality=70, optimize=True)
            thumbnail_b64 = base64.b64encode(buffer.getvalue()).decode("ascii")
    except Exception as exc:
        logger.debug("Desktop screen capture failed: %s", exc)

    active_app, window_title = _get_active_window_info(default_title)
    _cached_glance = (current_time, thumbnail_b64, active_app, window_title)

    return {
        "taskId": task_id,
        "attemptId": attempt_id,
        "timestamp": int(current_time * 1000),
        "thumbnailBase64": thumbnail_b64,
        "windowTitle": window_title,
        "activeApp": active_app,
    }
