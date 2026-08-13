from __future__ import annotations

from typing import Any

from knoa_platform.tools.base import ToolBase, ToolCapability, ToolEffect, ToolRisk


class HotkeyTool(ToolBase):
    name = "hotkey"
    description = "Send a key combination."
    effect = ToolEffect.DESKTOP_CONTROL
    capabilities = frozenset({ToolCapability.DESKTOP_CONTROL})
    risk = ToolRisk.HIGH

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

    def definition(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": {
                "type": "object",
                "properties": {
                    "keys": {"type": "array", "items": {"type": "string"}, "description": "e.g. [ctrl, c]"},
                },
                "required": ["keys"],
            },
        }

    def skim_definition(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": {
                "type": "object",
                "properties": {"keys": {"type": "array", "items": {"type": "string"}, "description": "e.g. [ctrl, c]"}},
                "required": ["keys"],
            },
        }
