from __future__ import annotations

from typing import Any

from pc_assistant.platform_ import get_platform
from pc_assistant.tools.base import ToolBase, ToolCapability, ToolEffect, ToolRisk


class TypeTextTool(ToolBase):
    name = "type_text"
    description = "Type text via keyboard."
    effect = ToolEffect.DESKTOP_CONTROL
    capabilities = frozenset({ToolCapability.DESKTOP_CONTROL})
    risk = ToolRisk.HIGH

    async def execute(self, **kwargs: Any) -> Any:
        text = kwargs.get("text", "")
        if not text:
            return {"error": "text is required"}
        try:
            import pyautogui
            import pyperclip

            # Use clipboard paste for reliability (handles Unicode)
            pyperclip.copy(text)
            plat = get_platform()
            if plat == "macos":
                pyautogui.hotkey("command", "v")
            else:
                pyautogui.hotkey("ctrl", "v")
            return {"success": True, "characters": len(text)}
        except ImportError as e:
            return {"error": f"Missing dependency: {e}"}
        except Exception as e:
            return {"error": f"Failed to type text: {e}"}

    def definition(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                },
                "required": ["text"],
            },
        }

    def skim_definition(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": {
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
            },
        }
