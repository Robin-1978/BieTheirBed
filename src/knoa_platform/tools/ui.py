"""Semantic GUI automation via the accessibility tree."""
from __future__ import annotations

import asyncio
from typing import Any

from knoa_platform.tools.base import (
    ToolBase,
    ToolCapability,
    ToolEffect,
    ToolPolicy,
    ToolRisk,
)
from knoa_platform.vision.a11y import A11yService


class UiTool(ToolBase):
    name = "ui"
    description = (
        "Inspect and interact with desktop UI elements by accessibility role/name "
        "instead of screen coordinates."
    )
    effect = ToolEffect.DESKTOP_CONTROL
    capabilities = frozenset({ToolCapability.DESKTOP_CONTROL})
    schema_capabilities = frozenset(
        {ToolCapability.DESKTOP_OBSERVE, ToolCapability.DESKTOP_CONTROL}
    )
    risk = ToolRisk.MEDIUM

    def __init__(self, *, ui_backend: str = "auto") -> None:
        self._service = A11yService(backend=ui_backend)

    def policy_for(self, arguments: dict[str, Any]) -> ToolPolicy:
        if arguments.get("action") == "snapshot":
            return ToolPolicy(
                effect=ToolEffect.READ_ONLY,
                capabilities=frozenset({ToolCapability.DESKTOP_OBSERVE}),
                risk=ToolRisk.LOW,
            )
        return self.policy

    async def execute(self, **kwargs: Any) -> Any:
        action = kwargs.get("action", "snapshot")
        handlers = {
            "snapshot": self._snapshot,
            "click": self._click,
            "fill": self._fill,
            "select": self._select,
            "focus": self._focus,
        }
        handler = handlers.get(action)
        if handler is None:
            return {
                "error": (
                    f"Unknown action: {action}. "
                    "Use: snapshot, click, fill, select, focus."
                )
            }
        return await handler(kwargs)

    def definition(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["snapshot", "click", "fill", "select", "focus"],
                        "description": "Accessibility action to perform.",
                    },
                    "window_name": {
                        "type": "string",
                        "description": "Optional window title filter for snapshot.",
                    },
                    "element_path": {
                        "type": "string",
                        "description": "Element path from snapshot or unique element name.",
                    },
                    "name": {
                        "type": "string",
                        "description": "Element label/name when element_path is omitted.",
                    },
                    "role": {
                        "type": "string",
                        "description": "Optional role filter when matching by name.",
                    },
                    "value": {
                        "type": "string",
                        "description": "Text value for fill/select actions.",
                    },
                },
                "required": ["action"],
            },
        }

    def skim_definition(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["snapshot", "click", "fill", "select", "focus"],
                    },
                    "window_name": {"type": "string"},
                    "element_path": {"type": "string"},
                    "name": {"type": "string"},
                    "value": {"type": "string"},
                },
                "required": ["action"],
            },
        }

    async def _snapshot(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        window_name = kwargs.get("window_name")
        try:
            elements = await asyncio.to_thread(
                self._service.get_accessible_tree,
                window_name or None,
            )
            return {
                "success": True,
                "window_name": window_name,
                "count": len(elements),
                "elements": [element.to_dict() for element in elements],
            }
        except LookupError as exc:
            return {"error": str(exc)}
        except RuntimeError as exc:
            return {"error": str(exc)}
        except Exception as exc:
            return {"error": f"Accessibility snapshot failed: {exc}"}

    async def _click(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        target = self._resolve_target(kwargs)
        if "error" in target:
            return target
        return await asyncio.to_thread(
            self._service.perform_action,
            target["element_path"],
            "click",
        )

    async def _fill(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        value = kwargs.get("value")
        if not value:
            return {"error": "value is required for fill action"}
        target = self._resolve_target(kwargs)
        if "error" in target:
            return target
        return await asyncio.to_thread(
            self._service.perform_action,
            target["element_path"],
            "fill",
            value,
        )

    async def _select(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        value = kwargs.get("value")
        if not value:
            return {"error": "value is required for select action"}
        target = self._resolve_target(kwargs)
        if "error" in target:
            return target
        return await asyncio.to_thread(
            self._service.perform_action,
            target["element_path"],
            "select",
            value,
        )

    async def _focus(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        target = self._resolve_target(kwargs)
        if "error" in target:
            return target
        return await asyncio.to_thread(
            self._service.perform_action,
            target["element_path"],
            "focus",
        )

    def _resolve_target(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        element_path = str(kwargs.get("element_path") or "").strip()
        if element_path:
            return {"element_path": element_path}
        name = str(kwargs.get("name") or "").strip()
        if not name:
            return {"error": "element_path or name is required"}
        role = str(kwargs.get("role") or "").strip().casefold()
        try:
            elements = self._service.get_accessible_tree(kwargs.get("window_name") or None)
        except RuntimeError as exc:
            return {"error": str(exc)}
        matches = [
            element
            for element in elements
            if name.casefold() in element.name.casefold()
            or element.name.casefold() in name.casefold()
        ]
        if role:
            matches = [element for element in matches if role in element.role.casefold()]
        if not matches:
            return {"error": f"Element not found: {name}"}
        if len(matches) > 1:
            exact = [element for element in matches if element.name.casefold() == name.casefold()]
            matches = exact or matches
        if len(matches) > 1:
            return {
                "error": f"Multiple elements matched '{name}'. Use element_path.",
                "candidates": [element.to_dict() for element in matches[:8]],
            }
        return {"element_path": matches[0].path, "element": matches[0].to_dict()}
