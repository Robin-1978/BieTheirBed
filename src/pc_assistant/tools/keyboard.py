from __future__ import annotations

import asyncio
from typing import Any

from pc_assistant.platform_ import get_platform
from pc_assistant.tools.base import ToolBase


class KeyboardTool(ToolBase):
    name = "keyboard"
    description = "Control keyboard: send key presses, hotkeys, and text input"

    # Key name mappings for cross-platform compatibility
    KEY_ALIASES = {
        "ctrl": "ctrl",
        "control": "ctrl",
        "alt": "alt",
        "shift": "shift",
        "super": "super",
        "win": "super",
        "cmd": "super",
        "command": "super",
        "meta": "super",
        "enter": "enter",
        "return": "enter",
        "tab": "tab",
        "space": "space",
        "backspace": "backspace",
        "delete": "delete",
        "del": "delete",
        "esc": "escape",
        "escape": "escape",
        "up": "up",
        "down": "down",
        "left": "left",
        "right": "right",
        "home": "home",
        "end": "end",
        "pageup": "pageup",
        "pagedown": "pagedown",
        "f1": "f1",
        "f2": "f2",
        "f3": "f3",
        "f4": "f4",
        "f5": "f5",
        "f6": "f6",
        "f7": "f7",
        "f8": "f8",
        "f9": "f9",
        "f10": "f10",
        "f11": "f11",
        "f12": "f12",
        "volumeup": "volumeup",
        "volumedown": "volumedown",
        "volumemute": "volumemute",
        "playpause": "playpause",
        "stop": "stop",
        "previous": "previous",
        "next": "next",
    }

    async def execute(self, **kwargs: Any) -> Any:
        action = kwargs.get("action", "press")
        handlers = {
            "press": self._press_key,
            "type": self._type_text,
            "hotkey": self._send_hotkey,
            "write": self._write_text,
            "shortcut": self._send_shortcut,
        }
        handler = handlers.get(action)
        if handler is None:
            return {"error": f"Unknown action: {action}. Use: press, type, hotkey, write, shortcut."}
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
                        "enum": ["press", "type", "hotkey", "write", "shortcut"],
                        "description": "Action: 'press' for single key, 'type' for text, 'hotkey' for modifier+key, 'write' for clipboard paste, 'shortcut' for key combination",
                    },
                    "key": {
                        "type": "string",
                        "description": "Key to press (e.g., 'enter', 'tab', 'a', 'f1')",
                    },
                    "keys": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Keys for hotkey/shortcut (e.g., ['ctrl', 'c'] for Ctrl+C)",
                    },
                    "text": {
                        "type": "string",
                        "description": "Text to type (for 'type' action)",
                    },
                    "hold_duration": {
                        "type": "number",
                        "description": "How long to hold the key in seconds (for 'press' action)",
                    },
                    "delay": {
                        "type": "number",
                        "description": "Delay between keys in seconds (for 'type' action)",
                    },
                },
                "required": ["action"],
            },
        }

    def _normalize_key(self, key: str) -> str:
        """Normalize key name to standard format."""
        key = key.lower().strip()
        return self.KEY_ALIASES.get(key, key)

    async def _press_key(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        key = kwargs.get("key", "")
        hold_duration = kwargs.get("hold_duration", 0)

        if not key:
            return {"error": "key is required for press action"}

        normalized_key = self._normalize_key(key)
        plat = get_platform()

        try:
            if plat == "windows":
                return await self._press_key_windows(normalized_key, hold_duration)
            elif plat == "macos":
                return await self._press_key_macos(normalized_key, hold_duration)
            else:
                return await self._press_key_linux(normalized_key, hold_duration)
        except Exception as e:
            return {"error": f"Failed to press key '{key}': {e}"}

    async def _press_key_windows(self, key: str, hold_duration: float) -> dict[str, Any]:
        try:
            import pyautogui

            pyautogui.keyDown(key)
            if hold_duration > 0:
                await asyncio.sleep(hold_duration)
            pyautogui.keyUp(key)
            return {"success": True, "key": key, "platform": "windows"}
        except ImportError:
            return {"error": "pyautogui not installed. Run: pip install pyautogui"}

    async def _press_key_macos(self, key: str, hold_duration: float) -> dict[str, Any]:
        try:
            import pyautogui

            pyautogui.keyDown(key)
            if hold_duration > 0:
                await asyncio.sleep(hold_duration)
            pyautogui.keyUp(key)
            return {"success": True, "key": key, "platform": "macos"}
        except ImportError:
            return {"error": "pyautogui not installed. Run: pip install pyautogui"}

    async def _press_key_linux(self, key: str, hold_duration: float) -> dict[str, Any]:
        try:
            import pyautogui

            pyautogui.keyDown(key)
            if hold_duration > 0:
                await asyncio.sleep(hold_duration)
            pyautogui.keyUp(key)
            return {"success": True, "key": key, "platform": "linux"}
        except ImportError:
            return {"error": "pyautogui not installed. Run: pip install pyautogui"}

    async def _type_text(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        text = kwargs.get("text", "")
        delay = kwargs.get("delay", 0)

        if not text:
            return {"error": "text is required for type action"}

        plat = get_platform()

        try:
            if plat == "windows":
                return await self._type_text_windows(text, delay)
            elif plat == "macos":
                return await self._type_text_macos(text, delay)
            else:
                return await self._type_text_linux(text, delay)
        except Exception as e:
            return {"error": f"Failed to type text: {e}"}

    async def _type_text_windows(self, text: str, delay: float) -> dict[str, Any]:
        try:
            import pyautogui

            if delay > 0:
                pyautogui.write(text, interval=delay)
            else:
                pyautogui.write(text)
            return {"success": True, "characters": len(text), "platform": "windows"}
        except ImportError:
            return {"error": "pyautogui not installed"}

    async def _type_text_macos(self, text: str, delay: float) -> dict[str, Any]:
        try:
            import pyautogui

            if delay > 0:
                pyautogui.write(text, interval=delay)
            else:
                pyautogui.write(text)
            return {"success": True, "characters": len(text), "platform": "macos"}
        except ImportError:
            return {"error": "pyautogui not installed"}

    async def _type_text_linux(self, text: str, delay: float) -> dict[str, Any]:
        try:
            import pyautogui

            if delay > 0:
                pyautogui.write(text, interval=delay)
            else:
                pyautogui.write(text)
            return {"success": True, "characters": len(text), "platform": "linux"}
        except ImportError:
            return {"error": "pyautogui not installed"}

    async def _send_hotkey(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        keys = kwargs.get("keys", [])

        if not keys:
            return {"error": "keys array is required for hotkey action"}

        if len(keys) < 2:
            return {"error": "hotkey requires at least 2 keys (modifier + key)"}

        normalized_keys = [self._normalize_key(k) for k in keys]
        plat = get_platform()

        try:
            if plat == "windows":
                return await self._send_hotkey_pyautogui(normalized_keys)
            elif plat == "macos":
                return await self._send_hotkey_pyautogui(normalized_keys)
            else:
                return await self._send_hotkey_pyautogui(normalized_keys)
        except Exception as e:
            return {"error": f"Failed to send hotkey: {e}"}

    async def _send_hotkey_pyautogui(self, keys: list[str]) -> dict[str, Any]:
        try:
            import pyautogui

            # pyautogui.hotkey expects keys as separate arguments
            pyautogui.hotkey(*keys)
            return {"success": True, "keys": keys, "platform": "cross-platform"}
        except ImportError:
            return {"error": "pyautogui not installed. Run: pip install pyautogui"}

    async def _write_text(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        """Write text using clipboard paste (Ctrl+V / Cmd+V)."""
        text = kwargs.get("text", "")

        if not text:
            return {"error": "text is required for write action"}

        plat = get_platform()

        try:
            # First copy to clipboard
            import pyperclip

            old_clipboard = pyperclip.paste()
            pyperclip.copy(text)

            # Then paste
            if plat == "windows":
                import pyautogui
                pyautogui.hotkey("ctrl", "v")
            elif plat == "macos":
                import pyautogui
                pyautogui.hotkey("command", "v")
            else:
                import pyautogui
                pyautogui.hotkey("ctrl", "v")

            # Optionally restore old clipboard
            # pyperclip.copy(old_clipboard)

            return {"success": True, "characters": len(text), "method": "clipboard_paste"}
        except ImportError as e:
            return {"error": f"Missing dependency: {e}"}
        except Exception as e:
            return {"error": f"Failed to write text: {e}"}

    async def _send_shortcut(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        """Send a keyboard shortcut (alias for hotkey)."""
        return await self._send_hotkey(kwargs)
