"""Simple user-facing full-desktop screenshot artifact."""
from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

from knoa_platform.artifacts import ArtifactStore
from knoa_platform.context.scope import current_memory_scope
from knoa_platform.tools.artifacts import ArtifactPaths
from knoa_platform.tools.base import ToolBase, ToolCapability, ToolEffect, ToolRisk


class ScreenshotTool(ToolBase):
    name = "screenshot"
    description = (
        "Capture the full desktop and immediately attach it to the user's reply. "
        "One successful call already delivers the image; do not call screenshot, "
        "read_artifact, or attach again."
    )
    effect = ToolEffect.READ_ONLY
    capabilities = frozenset({ToolCapability.DESKTOP_OBSERVE})
    risk = ToolRisk.LOW

    def __init__(self, store: ArtifactStore, artifact_dir: str | Path) -> None:
        self._store = store
        self._paths = ArtifactPaths(artifact_dir)

    async def execute(self, **kwargs: Any) -> Any:
        save_path = self._paths.allocate(prefix="screenshot", suffix=".jpg")
        try:
            import mss
            from PIL import Image

            with mss.mss() as sct:
                shot = sct.grab(sct.monitors[0])
                image = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
                max_width = 3200
                if image.width > max_width:
                    image = image.resize(
                        (max_width, max(1, round(image.height * max_width / image.width))),
                        Image.Resampling.LANCZOS,
                    )
                image.save(
                    save_path,
                    format="JPEG",
                    quality=85,
                    optimize=True,
                    progressive=True,
                )
            artifact = self._store.register_generated(
                current_memory_scope().session_id,
                save_path,
                media_type="image/jpeg",
                retention="temporary",
            )
            artifact_id = str(artifact["artifact_id"])
            return {
                "success": True,
                "artifact": artifact,
                "delivery": "already_attached_to_user_reply",
                "instruction": "Reply now; do not capture, read, or attach another image.",
                "image_ref": self._store.reference(
                    current_memory_scope().session_id,
                    artifact_id,
                    caption="Latest full-desktop screenshot",
                ),
            }
        except ImportError:
            save_path.unlink(missing_ok=True)
            return {"error": "Screen capture unavailable (mss or Pillow not installed)"}
        except Exception as exc:
            save_path.unlink(missing_ok=True)
            return {"error": f"Screen capture failed: {exc}"}

    async def consume_desktop_companion_result(self, result: dict[str, Any]) -> Any:
        encoded = result.get("content_base64")
        if not isinstance(encoded, str) or result.get("media_type") != "image/jpeg":
            return {"error": "Desktop Companion returned an invalid screenshot"}
        save_path = self._paths.allocate(prefix="screenshot", suffix=".jpg")
        try:
            content = base64.b64decode(encoded, validate=True)
            if not content or len(content) > 32 * 1024 * 1024:
                raise ValueError("invalid screenshot size")
            save_path.write_bytes(content)
            artifact = self._store.register_generated(
                current_memory_scope().session_id,
                save_path,
                media_type="image/jpeg",
                retention="temporary",
            )
            artifact_id = str(artifact["artifact_id"])
            return {
                "success": True,
                "artifact": artifact,
                "delivery": "already_attached_to_user_reply",
                "instruction": "Reply now; do not capture, read, or attach another image.",
                "image_ref": self._store.reference(
                    current_memory_scope().session_id,
                    artifact_id,
                    caption="Latest full-desktop screenshot",
                ),
            }
        except (OSError, ValueError, TypeError):
            save_path.unlink(missing_ok=True)
            return {"error": "Desktop Companion returned an invalid screenshot"}

    def definition(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": {"type": "object", "properties": {}},
        }
