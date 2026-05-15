from __future__ import annotations

from typing import Any

from pc_assistant.platform_ import get_platform
from pc_assistant.tools.base import ToolBase


class WindowTool(ToolBase):
    name = "window"
    description = "Manage windows: list, focus, move, resize, minimize, maximize, close"

    async def execute(self, **kwargs: Any) -> Any:
        action = kwargs.get("action", "list")
        handlers = {
            "list": self._list_windows,
            "info": self._window_info,
            "focus": self._focus_window,
            "move": self._move_window,
            "resize": self._resize_window,
            "minimize": self._minimize_window,
            "maximize": self._maximize_window,
            "restore": self._restore_window,
            "close": self._close_window,
            "screenshot": self._window_screenshot,
        }
        handler = handlers.get(action)
        if handler is None:
            return {"error": f"Unknown action: {action}. Use: list, info, focus, move, resize, minimize, maximize, restore, close, screenshot."}
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
                        "enum": ["list", "info", "focus", "move", "resize", "minimize", "maximize", "restore", "close", "screenshot"],
                        "description": "Action to perform on windows",
                    },
                    "window_id": {
                        "type": "string",
                        "description": "Window identifier (title, class name, or partial match)",
                    },
                    "x": {
                        "type": "integer",
                        "description": "X coordinate (for move action)",
                    },
                    "y": {
                        "type": "integer",
                        "description": "Y coordinate (for move action)",
                    },
                    "width": {
                        "type": "integer",
                        "description": "Width (for resize action)",
                    },
                    "height": {
                        "type": "integer",
                        "description": "Height (for resize action)",
                    },
                    "save_path": {
                        "type": "string",
                        "description": "Path to save screenshot (for screenshot action)",
                    },
                },
                "required": ["action"],
            },
        }

    def _get_window_by_id(self, window_id: str) -> Any | None:
        """Find window by ID (title, class, or partial match)."""
        try:
            import pywinctl as pwc
        except ImportError:
            return None

        # Try exact match first
        for window in pwc.getAllWindows():
            if window.title == window_id or window.title.lower() == window_id.lower():
                return window
            if window.className and window.className.lower() == window_id.lower():
                return window

        # Try partial match
        window_id_lower = window_id.lower()
        matches = []
        for window in pwc.getAllWindows():
            if window_id_lower in window.title.lower():
                matches.append(window)

        if len(matches) == 1:
            return matches[0]
        elif len(matches) > 1:
            return matches[0]  # Return first match, user can be more specific

        return None

    async def _list_windows(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        plat = get_platform()
        if plat == "windows":
            return await self._list_windows_win32(kwargs)
        elif plat == "macos":
            return await self._list_windows_macos(kwargs)
        else:
            return await self._list_windows_linux(kwargs)

    async def _list_windows_win32(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        try:
            import pywinctl as pwc
        except ImportError:
            return {"error": "pywinctl not installed. Run: pip install pywinctl"}

        try:
            windows = []
            for window in pwc.getAllWindows():
                if window.title:  # Skip untitled windows
                    bounds = window.bounds
                    windows.append({
                        "title": window.title,
                        "class_name": window.className or "",
                        "is_visible": window.isVisible,
                        "is_minimized": window.isMinimized,
                        "is_maximized": window.isMaximized,
                        "x": bounds.x,
                        "y": bounds.y,
                        "width": bounds.width,
                        "height": bounds.height,
                        "process_id": window.processID,
                    })

            # Sort by title
            windows.sort(key=lambda w: w["title"].lower())

            return {
                "windows": windows,
                "count": len(windows),
            }
        except Exception as e:
            return {"error": f"Failed to list windows: {e}"}

    async def _list_windows_macos(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        try:
            import pywinctl as pwc
        except ImportError:
            return {"error": "pywinctl not installed. Run: pip install pywinctl"}

        try:
            windows = []
            for window in pwc.getAllWindows():
                if window.title:
                    bounds = window.bounds
                    windows.append({
                        "title": window.title,
                        "is_visible": window.isVisible,
                        "is_minimized": window.isMinimized,
                        "x": bounds.x,
                        "y": bounds.y,
                        "width": bounds.width,
                        "height": bounds.height,
                    })

            windows.sort(key=lambda w: w["title"].lower())
            return {
                "windows": windows,
                "count": len(windows),
            }
        except Exception as e:
            return {"error": f"Failed to list windows: {e}"}

    async def _list_windows_linux(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        try:
            import pywinctl as pwc
        except ImportError:
            return {"error": "pywinctl not installed. Run: pip install pywinctl"}

        try:
            windows = []
            for window in pwc.getAllWindows():
                if window.title:
                    bounds = window.bounds
                    windows.append({
                        "title": window.title,
                        "is_visible": window.isVisible,
                        "is_minimized": window.isMinimized,
                        "x": bounds.x,
                        "y": bounds.y,
                        "width": bounds.width,
                        "height": bounds.height,
                    })

            windows.sort(key=lambda w: w["title"].lower())
            return {
                "windows": windows,
                "count": len(windows),
            }
        except Exception as e:
            return {"error": f"Failed to list windows: {e}"}

    async def _window_info(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        window_id = kwargs.get("window_id", "")
        if not window_id:
            return {"error": "window_id is required for info action"}

        try:
            import pywinctl as pwc
        except ImportError:
            return {"error": "pywinctl not installed"}

        window = self._get_window_by_id(window_id)
        if window is None:
            return {"error": f"Window not found: {window_id}"}

        bounds = window.bounds
        return {
            "title": window.title,
            "class_name": getattr(window, 'className', '') or "",
            "is_visible": window.isVisible,
            "is_minimized": window.isMinimized,
            "is_maximized": window.isMaximized,
            "is_active": window.isActive,
            "x": bounds.x,
            "y": bounds.y,
            "width": bounds.width,
            "height": bounds.height,
            "process_id": getattr(window, 'processID', 0),
        }

    async def _focus_window(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        window_id = kwargs.get("window_id", "")
        if not window_id:
            return {"error": "window_id is required for focus action"}

        try:
            import pywinctl as pwc
        except ImportError:
            return {"error": "pywinctl not installed"}

        window = self._get_window_by_id(window_id)
        if window is None:
            return {"error": f"Window not found: {window_id}"}

        try:
            window.activate()
            return {
                "success": True,
                "title": window.title,
                "message": f"Focused window: {window.title}",
            }
        except Exception as e:
            return {"error": f"Failed to focus window: {e}"}

    async def _move_window(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        window_id = kwargs.get("window_id", "")
        x = kwargs.get("x")
        y = kwargs.get("y")

        if not window_id:
            return {"error": "window_id is required"}
        if x is None or y is None:
            return {"error": "x and y coordinates are required for move action"}

        try:
            import pywinctl as pwc
        except ImportError:
            return {"error": "pywinctl not installed"}

        window = self._get_window_by_id(window_id)
        if window is None:
            return {"error": f"Window not found: {window_id}"}

        try:
            window.position = (x, y)
            return {
                "success": True,
                "title": window.title,
                "x": x,
                "y": y,
                "message": f"Moved window '{window.title}' to ({x}, {y})",
            }
        except Exception as e:
            return {"error": f"Failed to move window: {e}"}

    async def _resize_window(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        window_id = kwargs.get("window_id", "")
        width = kwargs.get("width")
        height = kwargs.get("height")

        if not window_id:
            return {"error": "window_id is required"}
        if width is None or height is None:
            return {"error": "width and height are required for resize action"}

        try:
            import pywinctl as pwc
        except ImportError:
            return {"error": "pywinctl not installed"}

        window = self._get_window_by_id(window_id)
        if window is None:
            return {"error": f"Window not found: {window_id}"}

        try:
            window.size = (width, height)
            return {
                "success": True,
                "title": window.title,
                "width": width,
                "height": height,
                "message": f"Resized window '{window.title}' to {width}x{height}",
            }
        except Exception as e:
            return {"error": f"Failed to resize window: {e}"}

    async def _minimize_window(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        window_id = kwargs.get("window_id", "")
        if not window_id:
            return {"error": "window_id is required"}

        try:
            import pywinctl as pwc
        except ImportError:
            return {"error": "pywinctl not installed"}

        window = self._get_window_by_id(window_id)
        if window is None:
            return {"error": f"Window not found: {window_id}"}

        try:
            window.minimize()
            return {
                "success": True,
                "title": window.title,
                "message": f"Minimized window: {window.title}",
            }
        except Exception as e:
            return {"error": f"Failed to minimize window: {e}"}

    async def _maximize_window(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        window_id = kwargs.get("window_id", "")
        if not window_id:
            return {"error": "window_id is required"}

        try:
            import pywinctl as pwc
        except ImportError:
            return {"error": "pywinctl not installed"}

        window = self._get_window_by_id(window_id)
        if window is None:
            return {"error": f"Window not found: {window_id}"}

        try:
            window.maximize()
            return {
                "success": True,
                "title": window.title,
                "message": f"Maximized window: {window.title}",
            }
        except Exception as e:
            return {"error": f"Failed to maximize window: {e}"}

    async def _restore_window(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        window_id = kwargs.get("window_id", "")
        if not window_id:
            return {"error": "window_id is required"}

        try:
            import pywinctl as pwc
        except ImportError:
            return {"error": "pywinctl not installed"}

        window = self._get_window_by_id(window_id)
        if window is None:
            return {"error": f"Window not found: {window_id}"}

        try:
            window.restore()
            return {
                "success": True,
                "title": window.title,
                "message": f"Restored window: {window.title}",
            }
        except Exception as e:
            return {"error": f"Failed to restore window: {e}"}

    async def _close_window(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        window_id = kwargs.get("window_id", "")
        if not window_id:
            return {"error": "window_id is required"}

        try:
            import pywinctl as pwc
        except ImportError:
            return {"error": "pywinctl not installed"}

        window = self._get_window_by_id(window_id)
        if window is None:
            return {"error": f"Window not found: {window_id}"}

        try:
            window.close()
            return {
                "success": True,
                "title": window.title,
                "message": f"Closed window: {window.title}",
            }
        except Exception as e:
            return {"error": f"Failed to close window: {e}"}

    async def _window_screenshot(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        window_id = kwargs.get("window_id", "")
        save_path = kwargs.get("save_path", "window_screenshot.png")

        try:
            import pywinctl as pwc
        except ImportError:
            return {"error": "pywinctl not installed"}

        window = self._get_window_by_id(window_id) if window_id else None

        try:
            import mss
            from PIL import Image

            with mss.mss() as sct:
                if window:
                    # Get window bounds
                    bounds = window.bounds
                    monitor = {
                        "left": bounds.x,
                        "top": bounds.y,
                        "width": bounds.width,
                        "height": bounds.height,
                    }
                else:
                    # Full screen
                    monitor = sct.monitors[0]

                shot = sct.grab(monitor)
                img = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
                img.save(save_path)
                return {
                    "success": True,
                    "path": save_path,
                    "size": shot.size,
                    "window": window.title if window else "full_screen",
                }
        except ImportError:
            return {"error": "mss or Pillow not installed"}
        except Exception as e:
            return {"error": f"Failed to capture screenshot: {e}"}
