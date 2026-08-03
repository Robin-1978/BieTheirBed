from __future__ import annotations

import platform
from typing import Any

from pc_assistant.platform_ import get_platform
from pc_assistant.tools.base import ToolBase


class SystemTool(ToolBase):
    name = "system"
    description = "Get system information and capture screenshots"

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
        save_path = kwargs.get("path", "screenshot.png")
        try:
            import mss

            with mss.mss() as sct:
                monitor = sct.monitors[0]
                shot = sct.grab(monitor)
                from PIL import Image

                img = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
                img.save(save_path)
                return {"success": True, "path": save_path, "size": shot.size}
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
