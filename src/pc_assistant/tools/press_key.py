from __future__ import annotations

from typing import Any

from pc_assistant.tools.base import ToolBase


class PressKeyTool(ToolBase):
    name = "press_key"
    description = "Press a single key."
    is_side_effecting = True

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

    def schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "key": {"type": "string", "description": "e.g. enter, tab, f1, a"},
                },
                "required": ["key"],
            },
        }
