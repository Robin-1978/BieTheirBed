from __future__ import annotations

from typing import Any

from pc_assistant.tools.base import ToolBase


class HotkeyTool(ToolBase):
    name = "hotkey"
    description = "Send a key combination."
    is_side_effecting = True

    async def execute(self, **kwargs: Any) -> Any:
        keys = kwargs.get("keys", [])
        if not keys:
            return {"error": "keys array is required"}
        if len(keys) < 2:
            return {"error": "hotkey requires at least 2 keys"}
        normalized = [k.lower().strip() for k in keys]
        try:
            import pyautogui
            pyautogui.hotkey(*normalized)
            return {"success": True, "keys": normalized}
        except ImportError:
            return {"error": "pyautogui not installed"}
        except Exception as e:
            return {"error": f"Failed to send hotkey: {e}"}

    def schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "keys": {"type": "array", "items": {"type": "string"}, "description": "e.g. [ctrl, c]"},
                },
                "required": ["keys"],
            },
        }

    def skim_schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {"keys": {"type": "array", "items": {"type": "string"}, "description": "e.g. [ctrl, c]"}},
                "required": ["keys"],
            },
        }
