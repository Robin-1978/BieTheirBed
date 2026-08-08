from __future__ import annotations

from typing import Any

from pc_assistant.tools.base import (
    ToolBase,
    ToolCapability,
    ToolEffect,
    ToolPolicy,
    ToolRisk,
)


class ClipboardTool(ToolBase):
    name = "clipboard"
    description = "Read or write the system clipboard."
    effect = ToolEffect.DESKTOP_CONTROL
    capabilities = frozenset({ToolCapability.DESKTOP_CONTROL})
    schema_capabilities = frozenset({ToolCapability.DESKTOP_OBSERVE})
    risk = ToolRisk.MEDIUM

    def policy_for(self, arguments: dict[str, Any]) -> ToolPolicy:
        if arguments.get("action") == "read":
            return ToolPolicy(
                effect=ToolEffect.READ_ONLY,
                capabilities=frozenset({ToolCapability.DESKTOP_OBSERVE}),
                risk=ToolRisk.LOW,
            )
        return self.policy

    async def execute(self, **kwargs: Any) -> Any:
        action = kwargs.get("action")
        handlers = {
            "read": self._read,
            "write": self._write,
        }
        handler = handlers.get(action)
        if handler is None:
            return {"error": f"Unknown clipboard action: {action}"}
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
                        "enum": ["read", "write"],
                    },
                    "content": {"type": "string", "description": "Content to write to clipboard"},
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
                    "action": {"type": "string", "enum": ["read", "write"]},
                    "content": {"type": "string", "description": "for write"},
                },
                "required": ["action"],
            },
        }

    def _read(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        try:
            import pyperclip

            content = pyperclip.paste()
            return {"content": content}
        except pyperclip.PyperclipException as e:
            return {"error": f"Clipboard access error: {e}"}
        except ImportError:
            return {"error": "pyperclip not installed"}

    def _write(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        content = kwargs.get("content", "")
        try:
            import pyperclip

            pyperclip.copy(content)
            return {"success": True}
        except pyperclip.PyperclipException as e:
            return {"error": f"Clipboard access error: {e}"}
        except ImportError:
            return {"error": "pyperclip not installed"}
