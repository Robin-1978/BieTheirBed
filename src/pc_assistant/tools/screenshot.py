"""Simple user-facing full-desktop screenshot artifact."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from pc_assistant.artifacts import ArtifactStore
from pc_assistant.context.scope import current_memory_scope
from pc_assistant.tools.artifacts import ArtifactPaths
from pc_assistant.tools.base import ToolBase


class ScreenshotTool(ToolBase):
    name = "screenshot"
    description = (
        "Capture the full desktop as a PNG artifact for the current user. "
        "Use this when the user asks to take, show, send, or attach a screenshot."
    )
    is_side_effecting = True

    def __init__(self, store: ArtifactStore, artifact_dir: str | Path) -> None:
        self._store = store
        self._paths = ArtifactPaths(artifact_dir)

    async def execute(self, **kwargs: Any) -> Any:
        save_path = self._paths.allocate(prefix="screenshot", suffix=".png")
        try:
            import mss
            from PIL import Image

            with mss.mss() as sct:
                shot = sct.grab(sct.monitors[0])
                image = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
                image.save(save_path, format="PNG")
            artifact = self._store.register_generated(
                current_memory_scope().session_id,
                save_path,
                media_type="image/png",
                retention="temporary",
            )
            return {"success": True, "artifact": artifact}
        except ImportError:
            save_path.unlink(missing_ok=True)
            return {"error": "Screen capture unavailable (mss or Pillow not installed)"}
        except Exception as exc:
            save_path.unlink(missing_ok=True)
            return {"error": f"Screen capture failed: {exc}"}

    def schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        }

    def core_schema(self) -> dict[str, Any]:
        return self.schema()
