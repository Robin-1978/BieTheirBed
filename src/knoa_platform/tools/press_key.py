from __future__ import annotations

from typing import Any

from knoa_platform.tools.base import ToolBase, ToolCapability, ToolEffect, ToolRisk


class PressKeyTool(ToolBase):
    name = "press_key"
    description = "Press a single key."
    effect = ToolEffect.DESKTOP_CONTROL
    capabilities = frozenset({ToolCapability.DESKTOP_CONTROL})
    risk = ToolRisk.HIGH

    async def execute(self, **kwargs: Any) -> Any:
        key = kwargs.get("key", "")
        if not key:
            return {"error": "key is required"}
        key = key.lower().strip()
        try:
            import pyautogui
            pyautogui.press(key)
            return {"success": True, "key": key}
        except ImportError:
            return {"error": "pyautogui not installed"}
        except Exception as e:
            return {"error": f"Failed to press key '{key}': {e}"}

    def definition(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": {
                "type": "object",
                "properties": {
                    "key": {"type": "string", "description": "e.g. enter, tab, f1, a"},
                },
                "required": ["key"],
            },
        }

    def skim_definition(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": {
                "type": "object",
                "properties": {"key": {"type": "string", "description": "e.g. enter, tab, f1, a"}},
                "required": ["key"],
            },
        }
