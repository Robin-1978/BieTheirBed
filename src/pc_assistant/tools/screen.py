"""Visual layer tool: screenshot + grid overlay + post-action verification.

Fallback for the semantic ``ui`` tool. ``inspect_screen.look`` returns an inline image
block (optionally with a coordinate grid) plus the metadata the model needs to
convert grid cells into pyautogui coordinates. ``inspect_screen.verify`` re-captures
after an action so the model can self-check the result (Look → Act → Verify).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from pc_assistant.tools.artifacts import ArtifactPaths, image_artifact
from pc_assistant.tools.base import ToolBase
from pc_assistant.vision import preprocess
from pc_assistant.vision.coordinates import CoordinateTransform


class ScreenTool(ToolBase):
    name = "screen"
    description = "Get screen resolution and display info."

    def __init__(
        self,
        *,
        grid_enabled: bool = False,
        max_side: int = 1280,
        jpeg_quality: int = 70,
        artifact_dir: str | Path | None = None,
    ) -> None:
        self._grid_enabled = grid_enabled
        self._max_side = max_side
        self._jpeg_quality = jpeg_quality
        self._artifacts = ArtifactPaths(artifact_dir)

    async def execute(self, **kwargs: Any) -> Any:
        action = kwargs.get("action", "look")
        handlers = {
            "look": self._look,
            "verify": self._verify,
            "info": self._info,
        }
        handler = handlers.get(action)
        if handler is None:
            return {"error": f"Unknown action: {action}. Use: look, verify, info."}
        return handler(kwargs)

    def schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["look", "verify", "info"],
                        "description": "look: screenshot (with grid); verify: re-capture to confirm a change; info: resolution",
                    },
                    "region_x": {"type": "integer", "description": "Crop left (with region_y/width/height)"},
                    "region_y": {"type": "integer", "description": "Crop top (with region_x/width/height)"},
                    "region_width": {"type": "integer", "description": "Crop width"},
                    "region_height": {"type": "integer", "description": "Crop height"},
                    "grid": {"type": "boolean", "description": "Overlay a coordinate grid (columns A.., rows 1..)"},
                    "path": {"type": "string", "description": "Save path for the screenshot"},
                    "cols": {"type": "integer", "description": "Grid columns (default 10)"},
                    "rows": {"type": "integer", "description": "Grid rows (default 10)"},
                },
                "required": ["action"],
            },
        }

    def skim_schema(self) -> dict[str, Any]:
        return self.schema()

    # ------------------------------------------------------------------

    def _capture(
        self,
        kwargs: dict[str, Any],
        *,
        grid_default: bool,
    ) -> dict[str, Any]:
        region = None
        if any(kwargs.get(key) is not None for key in ("region_x", "region_y", "region_width", "region_height")):
            region = {
                "x": kwargs.get("region_x"),
                "y": kwargs.get("region_y"),
                "width": kwargs.get("region_width"),
                "height": kwargs.get("region_height"),
            }
        use_grid = bool(kwargs.get("grid", grid_default))
        action = str(kwargs.get("action", "look"))
        try:
            save_path = self._artifacts.allocate(
                prefix=f"screen-{action}",
                suffix=".png",
                requested=kwargs.get("path"),
            )
        except ValueError as exc:
            return {"error": str(exc)}

        grid_cols = int(kwargs.get("cols") or 10)
        grid_rows = int(kwargs.get("rows") or 10)

        block = preprocess.capture_block(
            region,
            max_side=self._max_side,
            quality=self._jpeg_quality,
            grid=use_grid,
            grid_cols=grid_cols,
            grid_rows=grid_rows,
        )
        if block is None:
            return {"error": "Screen capture unavailable (mss or Pillow not installed)."}

        try:
            import base64

            block_data = block["image_url"].split(",", 1)[1]
            image_bytes = base64.b64decode(block_data, validate=True)
            temporary = save_path.with_name(f".{save_path.name}.tmp")
            temporary.write_bytes(image_bytes)
            temporary.replace(save_path)
        except Exception as exc:
            return {"error": f"Failed to store screen capture: {exc}"}

        image_w = block.get("width") or 0
        image_h = block.get("height") or 0
        screen_w = block.get("source_width") or image_w
        screen_h = block.get("source_height") or image_h
        desktop_x = 0
        desktop_y = 0
        if region:
            screen_w = int(region.get("width", screen_w))
            screen_h = int(region.get("height", screen_h))
            desktop_x = int(region.get("x", 0))
            desktop_y = int(region.get("y", 0))
        transform = CoordinateTransform(
            image_width=int(image_w),
            image_height=int(image_h),
            desktop_x=desktop_x,
            desktop_y=desktop_y,
            desktop_width=int(screen_w),
            desktop_height=int(screen_h),
        )

        result: dict[str, Any] = {
            "success": True,
            "path": str(save_path),
            "artifact": image_artifact(save_path, "image/png"),
            "image": block,
            "screen_size": {"width": screen_w, "height": screen_h},
            "image_size": {"width": image_w, "height": image_h},
            "coordinate_transform": transform.to_dict(),
            "coordinate_note": (
                "The image is grid-aligned to the real screen. If the grid is enabled, "
                "reference elements by cell (e.g. B4) or pixel coordinates (0,0 = top-left)."
            ),
        }
        if use_grid:
            result["grid"] = {"cols": grid_cols, "rows": grid_rows, "enabled": True}
        return result

    def _look(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        return self._capture(kwargs, grid_default=self._grid_enabled)

    def _verify(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        result = self._capture(kwargs, grid_default=False)
        if "error" in result:
            return result
        result["verification"] = (
            "Fresh capture after the action. Compare this to the previous screenshot "
            "and confirm the expected change occurred."
        )
        return result

    def _info(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        try:
            import mss
        except ImportError:
            return {"error": "mss not installed"}

        try:
            with mss.mss() as sct:
                monitors = [
                    {"index": i, "left": m["left"], "top": m["top"], "width": m["width"], "height": m["height"]}
                    for i, m in enumerate(sct.monitors)
                ]
            return {
                "success": True,
                "monitors": monitors,
                "primary": {
                    "width": monitors[1]["width"] if len(monitors) > 1 else monitors[0]["width"],
                    "height": monitors[1]["height"] if len(monitors) > 1 else monitors[0]["height"],
                },
                "coordinate_system": "pyautogui-compatible pixel coordinates, origin top-left",
            }
        except Exception as e:
            return {"error": f"Failed to read screen info: {e}"}
