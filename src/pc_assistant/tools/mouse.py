from __future__ import annotations

from typing import Any

from pc_assistant.tools.base import ToolBase


class MouseTool(ToolBase):
    name = "mouse"
    description = "Control mouse: move, click, scroll, drag, and get cursor position"

    async def execute(self, **kwargs: Any) -> Any:
        action = kwargs.get("action", "position")
        handlers = {
            "position": self._get_position,
            "move": self._move_to,
            "click": self._click,
            "double_click": self._double_click,
            "right_click": self._right_click,
            "scroll": self._scroll,
            "drag": self._drag,
            "press": self._press,
            "release": self._release,
        }
        handler = handlers.get(action)
        if handler is None:
            return {"error": f"Unknown action: {action}. Use: position, move, click, double_click, right_click, scroll, drag."}
        return await handler(kwargs)

    def schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["position", "move", "click", "double_click", "right_click", "scroll", "drag", "press", "release"],
                        "description": "Action: 'position' for cursor location, 'move' to move cursor, 'click' to click, 'scroll' to scroll, 'drag' to drag",
                    },
                    "x": {
                        "type": "integer",
                        "description": "X coordinate (for move, click, drag actions)",
                    },
                    "y": {
                        "type": "integer",
                        "description": "Y coordinate (for move, click, drag actions)",
                    },
                    "dx": {
                        "type": "integer",
                        "description": "Horizontal scroll amount (for scroll action)",
                    },
                    "dy": {
                        "type": "integer",
                        "description": "Vertical scroll amount (for scroll action, positive=up, negative=down)",
                    },
                    "button": {
                        "type": "string",
                        "enum": ["left", "right", "middle"],
                        "description": "Mouse button (default: left)",
                    },
                    "x2": {
                        "type": "integer",
                        "description": "End X coordinate (for drag action)",
                    },
                    "y2": {
                        "type": "integer",
                        "description": "End Y coordinate (for drag action)",
                    },
                    "duration": {
                        "type": "number",
                        "description": "Duration in seconds for move/drag animation",
                    },
                },
                "required": ["action"],
            },
        }

    async def _get_position(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        try:
            import pyautogui

            x, y = pyautogui.position()
            return {
                "x": x,
                "y": y,
                "screen_size": {"width": pyautogui.size()[0], "height": pyautogui.size()[1]},
            }
        except ImportError:
            return {"error": "pyautogui not installed. Run: pip install pyautogui"}

    async def _move_to(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        x = kwargs.get("x")
        y = kwargs.get("y")
        duration = kwargs.get("duration", 0)

        if x is None or y is None:
            return {"error": "x and y coordinates are required for move action"}

        try:
            import pyautogui

            if duration > 0:
                pyautogui.moveTo(x, y, duration=duration)
            else:
                pyautogui.moveTo(x, y)
            return {
                "success": True,
                "x": x,
                "y": y,
                "duration": duration,
            }
        except ImportError:
            return {"error": "pyautogui not installed"}

    async def _click(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        x = kwargs.get("x")
        y = kwargs.get("y")
        button = kwargs.get("button", "left")

        try:
            import pyautogui

            if x is not None and y is not None:
                pyautogui.click(x, y, button=button)
                return {"success": True, "x": x, "y": y, "button": button}
            else:
                pyautogui.click(button=button)
                return {"success": True, "button": button}
        except ImportError:
            return {"error": "pyautogui not installed"}

    async def _double_click(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        x = kwargs.get("x")
        y = kwargs.get("y")

        try:
            import pyautogui

            if x is not None and y is not None:
                pyautogui.doubleClick(x, y)
                return {"success": True, "x": x, "y": y}
            else:
                pyautogui.doubleClick()
                return {"success": True}
        except ImportError:
            return {"error": "pyautogui not installed"}

    async def _right_click(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        x = kwargs.get("x")
        y = kwargs.get("y")

        try:
            import pyautogui

            if x is not None and y is not None:
                pyautogui.rightClick(x, y)
                return {"success": True, "x": x, "y": y}
            else:
                pyautogui.rightClick()
                return {"success": True}
        except ImportError:
            return {"error": "pyautogui not installed"}

    async def _scroll(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        dx = kwargs.get("dx", 0)
        dy = kwargs.get("dy", 0)
        x = kwargs.get("x")
        y = kwargs.get("y")

        try:
            import pyautogui

            if x is not None and y is not None:
                pyautogui.moveTo(x, y)
            pyautogui.scroll(dy, x=x, y=y)
            return {
                "success": True,
                "dx": dx,
                "dy": dy,
                "x": x,
                "y": y,
            }
        except ImportError:
            return {"error": "pyautogui not installed"}

    async def _drag(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        x = kwargs.get("x")
        y = kwargs.get("y")
        x2 = kwargs.get("x2")
        y2 = kwargs.get("y2")
        duration = kwargs.get("duration", 0.5)
        button = kwargs.get("button", "left")

        if x is None or y is None or x2 is None or y2 is None:
            return {"error": "x, y, x2, y2 coordinates are required for drag action"}

        try:
            import pyautogui

            # Move to start position
            pyautogui.moveTo(x, y)

            # Drag to end position
            pyautogui.drag(x2 - x, y2 - y, duration=duration, button=button)

            return {
                "success": True,
                "start": {"x": x, "y": y},
                "end": {"x": x2, "y": y2},
                "duration": duration,
            }
        except ImportError:
            return {"error": "pyautogui not installed"}

    async def _press(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        button = kwargs.get("button", "left")

        try:
            import pyautogui

            pyautogui.mouseDown(button=button)
            return {"success": True, "button": button, "action": "down"}
        except ImportError:
            return {"error": "pyautogui not installed"}

    async def _release(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        button = kwargs.get("button", "left")

        try:
            import pyautogui

            pyautogui.mouseUp(button=button)
            return {"success": True, "button": button, "action": "up"}
        except ImportError:
            return {"error": "pyautogui not installed"}
