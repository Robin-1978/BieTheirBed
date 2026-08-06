from __future__ import annotations

import asyncio
import subprocess
from typing import Any

from pc_assistant.platform_ import get_platform
from pc_assistant.tools.base import ToolBase


def _import_pywinctl():
    try:
        import pywinctl
    except ImportError:
        return None
    return pywinctl


class WindowTool(ToolBase):
    name = "windows"
    description = "List, focus, move, resize, or close desktop windows."

    async def execute(self, **kwargs: Any) -> Any:
        action = kwargs.get("action", "list")
        handlers = {
            "list": self._list_windows,
            "active": self._active_window,
            "info": self._window_info,
            "focus": self._focus_window,
            "move": self._move_window,
            "resize": self._resize_window,
            "minimize": self._minimize_window,
            "maximize": self._maximize_window,
            "restore": self._restore_window,
            "close": self._close_window,
        }
        handler = handlers.get(action)
        if handler is None:
            return {"error": f"Unknown action: {action}. Use: list, active, info, focus, move, resize, minimize, maximize, restore, close."}
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
                        "enum": ["list", "active", "info", "focus", "move", "resize", "minimize", "maximize", "restore", "close"],
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
                },
                "required": ["action"],
            },
        }

    def skim_schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["list", "focus", "move", "resize", "minimize", "maximize", "close"]},
                    "window_id": {"type": "string"},
                    "x": {"type": "integer"},
                    "y": {"type": "integer"},
                    "width": {"type": "integer"},
                    "height": {"type": "integer"},
                },
                "required": ["action"],
            },
        }

    def _get_window_by_id(self, window_id: str) -> Any | None:
        """Find one unambiguous window by title/app name; fail closed otherwise."""
        pwc = _import_pywinctl()
        if pwc is None:
            return None

        windows = list(pwc.getAllWindows())
        by_id = [window for window in windows if self._window_id(window) == window_id]
        if len(by_id) == 1:
            return by_id[0]
        needle = window_id.casefold()
        exact = [
            window for window in windows
            if str(window.title).casefold() == needle
            or self._app_name(window).casefold() == needle
        ]
        if len(exact) == 1:
            return exact[0]
        if len(exact) > 1:
            return None

        partial = [window for window in windows if needle in str(window.title).casefold()]
        return partial[0] if len(partial) == 1 else None

    def _box(self, window: Any) -> dict[str, Any] | None:
        """Cross-platform geometry. pywinctl exposes `box` on Linux/macOS and
        `bounds` on Windows; both are a Box(left, top, width, height)."""
        box = getattr(window, "box", None) or getattr(window, "bounds", None)
        if box is None:
            return None
        return {
            "x": box.left,
            "y": box.top,
            "width": box.width,
            "height": box.height,
        }

    def _app_name(self, window: Any) -> str:
        getter = getattr(window, "getAppName", None)
        if callable(getter):
            try:
                return str(getter())
            except Exception:
                pass
        for attr in ("className", "appName"):
            val = getattr(window, attr, None)
            if val:
                return str(val)
        return ""

    def _pid(self, window: Any) -> int:
        getter = getattr(window, "getPID", None)
        if callable(getter):
            try:
                return int(getter())
            except Exception:
                pass
        return getattr(window, "processID", 0)

    def _window_id(self, window: Any) -> str:
        for attr in ("_hWnd", "hWnd", "handle"):
            value = getattr(window, attr, None)
            if value not in (None, ""):
                return str(value)
        return f"{self._pid(window)}:{str(getattr(window, 'title', ''))}"

    def _window_record(self, window: Any) -> dict[str, Any] | None:
        box = self._box(window)
        if box is None:
            return None
        return {
            "window_id": self._window_id(window),
            "title": window.title,
            "app_name": self._app_name(window),
            "is_visible": bool(getattr(window, "isVisible", False)),
            "is_minimized": bool(getattr(window, "isMinimized", False)),
            "is_maximized": bool(getattr(window, "isMaximized", False)),
            "is_active": bool(getattr(window, "isActive", False)),
            "process_id": self._pid(window),
            **box,
        }

    def _enumerate_windows(self) -> list[dict[str, Any]]:
        import pywinctl as pwc

        records = []
        for window in pwc.getAllWindows():
            if not getattr(window, "title", ""):
                continue
            record = self._window_record(window)
            if record is not None:
                records.append(record)
        records.sort(key=lambda r: r["title"].lower())
        return records

    async def _list_windows(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        plat = get_platform()
        if plat == "windows":
            return await self._list_windows_win32(kwargs)
        elif plat == "macos":
            return await self._list_windows_macos(kwargs)
        else:
            return await self._list_windows_linux(kwargs)

    async def _active_window(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        pwc = _import_pywinctl()
        if pwc is None:
            return {"error": "pywinctl not installed. Run: pip install pywinctl"}
        try:
            window = pwc.getActiveWindow()
            if window is None:
                return {"found": False, "window": None}
            record = self._window_record(window)
            if record is None:
                return {"error": "Active window has no usable geometry"}
            record["is_active"] = True
            return {"found": True, "window": record}
        except Exception as e:
            return {"error": f"Failed to read active window: {e}"}

    async def _list_windows_win32(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        pwc = _import_pywinctl()
        if pwc is None:
            return {"error": "pywinctl not installed. Run: pip install pywinctl"}

        try:
            windows = self._enumerate_windows()
            return {"windows": windows, "count": len(windows)}
        except Exception as e:
            return {"error": f"Failed to list windows: {e}"}

    async def _list_windows_macos(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        pwc = _import_pywinctl()
        if pwc is None:
            return {"error": "pywinctl not installed. Run: pip install pywinctl"}

        try:
            windows = self._enumerate_windows()
            return {"windows": windows, "count": len(windows)}
        except Exception as e:
            return {"error": f"Failed to list windows: {e}"}

    async def _list_windows_linux(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        pwc = _import_pywinctl()
        if pwc is None:
            return {"error": "pywinctl not installed. Run: pip install pywinctl"}

        try:
            windows = self._enumerate_windows()
            return {"windows": windows, "count": len(windows)}
        except Exception as e:
            return {"error": f"Failed to list windows: {e}"}

    async def _window_info(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        window_id = kwargs.get("window_id", "")
        if not window_id:
            return {"error": "window_id is required for info action"}

        pwc = _import_pywinctl()
        if pwc is None:
            return {"error": "pywinctl not installed"}

        window = self._get_window_by_id(window_id)
        if window is None:
            return {"error": f"Window not found: {window_id}"}

        box = self._box(window)
        if box is None:
            return {"error": f"Window has no geometry: {window_id}"}

        return self._window_record(window)

    async def _focus_window(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        window_id = kwargs.get("window_id", "")
        if not window_id:
            return {"error": "window_id is required for focus action"}

        pwc = _import_pywinctl()
        if pwc is None:
            return {"error": "pywinctl not installed"}

        window = self._get_window_by_id(window_id)
        if window is None:
            return {"error": f"Window not found: {window_id}"}

        try:
            restored = bool(getattr(window, "isMinimized", False))
            if restored:
                window.restore()
            window.activate()
            await asyncio.sleep(0.15)
            active = pwc.getActiveWindow()
            verified = active is not None and (
                self._pid(active) == self._pid(window)
                or str(getattr(active, "title", "")) == str(window.title)
            )
            if not verified and get_platform() == "linux":
                # X11 window managers occasionally ignore the first EWMH
                # activation request, especially for minimized Electron apps.
                subprocess.run(
                    ["wmctrl", "-a", str(window.title)],
                    check=False,
                    capture_output=True,
                    timeout=3,
                )
                await asyncio.sleep(0.15)
                active = pwc.getActiveWindow()
                verified = active is not None and (
                    self._pid(active) == self._pid(window)
                    or str(getattr(active, "title", "")) == str(window.title)
                )
            if not verified:
                return {"error": f"Focus request was not verified for window: {window.title}"}
            return {
                "success": True,
                "title": window.title,
                "restored": restored,
                "verified_active": True,
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

        pwc = _import_pywinctl()
        if pwc is None:
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

        pwc = _import_pywinctl()
        if pwc is None:
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

        pwc = _import_pywinctl()
        if pwc is None:
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

        pwc = _import_pywinctl()
        if pwc is None:
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

        pwc = _import_pywinctl()
        if pwc is None:
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

        pwc = _import_pywinctl()
        if pwc is None:
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
