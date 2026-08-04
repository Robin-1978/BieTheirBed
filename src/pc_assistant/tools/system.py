from __future__ import annotations

import platform
from pathlib import Path
from typing import Any

from pc_assistant.platform_ import get_platform
from pc_assistant.tools.base import ToolBase
from pc_assistant.tools.artifacts import ArtifactPaths, image_artifact


class SystemTool(ToolBase):
    name = "system"
    description = "Get system information and capture screenshots"

    def __init__(self, artifact_dir: str | Path | None = None) -> None:
        self._artifacts = ArtifactPaths(artifact_dir)

    async def execute(self, **kwargs: Any) -> Any:
        action = kwargs.get("action")
        handlers = {
            "info": self._info,
            "screenshot": self._screenshot,
            "disk_usage": self._disk_usage,
        }
        handler = handlers.get(action)
        if handler is None:
            return {"error": f"Unknown system action: {action}"}
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
                        "enum": ["info", "screenshot", "disk_usage"],
                    },
                    "path": {"type": "string", "description": "Save path for screenshot"},
                    "inline": {"type": "boolean", "description": "Return the screenshot as an inline image block so the model can see it"},
                    "drive": {"type": "string", "description": "Drive letter for disk usage (Windows)"},
                },
                "required": ["action"],
            },
        }

    def core_schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": "System info, screenshots, disk usage.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["info", "screenshot", "disk_usage"]},
                    "path": {"type": "string"},
                    "inline": {"type": "boolean"},
                    "drive": {"type": "string"},
                },
                "required": ["action"],
            },
        }

    def _info(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        try:
            import psutil

            mem = psutil.virtual_memory()
            cpu_percent = psutil.cpu_percent(interval=0.1)
            return {
                "platform": platform.system(),
                "platform_release": platform.release(),
                "platform_version": platform.version(),
                "architecture": platform.machine(),
                "processor": platform.processor(),
                "cpu_count": psutil.cpu_count(),
                "cpu_percent": cpu_percent,
                "memory_total_gb": round(mem.total / (1024**3), 2),
                "memory_available_gb": round(mem.available / (1024**3), 2),
                "memory_percent": mem.percent,
            }
        except ImportError:
            return {
                "platform": platform.system(),
                "platform_release": platform.release(),
                "architecture": platform.machine(),
                "processor": platform.processor(),
            }

    def _screenshot(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        try:
            save_path = self._artifacts.allocate(
                prefix="system-screenshot",
                suffix=".png",
                requested=kwargs.get("path"),
            )
        except ValueError as exc:
            return {"error": str(exc)}
        inline = bool(kwargs.get("inline", False))
        try:
            import mss

            with mss.mss() as sct:
                monitor = sct.monitors[0]
                shot = sct.grab(monitor)
                from PIL import Image

                img = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
                img.save(save_path)
                result: dict[str, Any] = {
                    "success": True,
                    "path": str(save_path),
                    "size": shot.size,
                    "artifact": image_artifact(save_path, "image/png"),
                }
                if inline:
                    from pc_assistant.vision.preprocess import image_block_from_file

                    block = image_block_from_file(str(save_path))
                    if block is not None:
                        block["width"] = shot.size.width
                        block["height"] = shot.size.height
                        result["image"] = block
                return result
        except ImportError:
            return {"error": "mss or Pillow not installed"}
        except Exception as e:
            return {"error": str(e)}

    def _disk_usage(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        try:
            import psutil

            drive = kwargs.get("drive")
            current = get_platform()
            if current == "windows" and drive:
                path = f"{drive}:\\"
            else:
                path = drive or "/"
            usage = psutil.disk_usage(path)
            return {
                "path": path,
                "total_gb": round(usage.total / (1024**3), 2),
                "used_gb": round(usage.used / (1024**3), 2),
                "free_gb": round(usage.free / (1024**3), 2),
                "percent": usage.percent,
            }
        except ImportError:
            return {"error": "psutil not installed"}
        except Exception as e:
            return {"error": str(e)}
