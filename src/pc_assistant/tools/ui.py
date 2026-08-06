"""Semantic UI automation tool (accessibility-first).

Preferred over the visual layer: the model finds an element by name, then uses
the returned opaque ``element_id`` for actions. Falls back to the
visual ``inspect_screen`` tool when the a11y backend is unavailable.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from pc_assistant.tools.artifacts import ArtifactPaths, image_artifact
from pc_assistant.tools.base import ToolBase
from pc_assistant.vision import a11y
from pc_assistant.vision.targets import ElementRef, build_refs


class UITool(ToolBase):
    name = "ui"
    description = "Find and interact with UI elements on screen."
    is_side_effecting = True

    def __init__(
        self,
        ui_backend: str = "auto",
        *,
        snapshot_ttl_seconds: float = 5.0,
        clock=time.monotonic,
        artifact_dir: str | Path | None = None,
    ) -> None:
        self._ui_backend = ui_backend
        self._snapshot_ttl = max(0.1, snapshot_ttl_seconds)
        self._clock = clock
        self._snapshots: dict[str, tuple[float, dict[str, ElementRef]]] = {}
        self._artifacts = ArtifactPaths(artifact_dir)

    async def execute(self, **kwargs: Any) -> Any:
        action = kwargs.get("action", "list")
        handlers = {
            "list": self._list,
            "find": self._find,
            "click": self._click,
            "type": self._type_text,
            "screenshot_element": self._screenshot_element,
        }
        handler = handlers.get(action)
        if handler is None:
            return {"error": f"Unknown action: {action}. Use: list, find, click, type, screenshot_element."}
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
                        "enum": ["list", "find", "click", "type", "screenshot_element"],
                        "description": "Action to perform on the accessibility tree",
                    },
                    "element": {
                        "type": "string",
                        "description": "Element name or label used by find",
                    },
                    "element_id": {
                        "type": "string",
                        "description": "Opaque id returned by find/list; required by click, type, and screenshot_element",
                    },
                    "role": {"type": "string", "description": "Optional element role filter"},
                    "app": {
                        "type": "string",
                        "description": "Target application (partial title match)",
                    },
                    "text": {"type": "string", "description": "Text to type (for type action)"},
                    "double": {"type": "boolean", "description": "Double-click instead of single (for click action)"},
                    "enter": {"type": "boolean", "description": "Press Enter after typing (for type action)"},
                    "clear": {"type": "boolean", "description": "Select-all + delete before typing (for type action)"},
                    "save_path": {"type": "string", "description": "Path to save element screenshot"},
                    "inline": {"type": "boolean", "description": "Return the element screenshot as an inline image block"},
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
                    "action": {"type": "string", "enum": ["find", "click", "type", "list"]},
                    "element": {"type": "string", "description": "element name or label"},
                    "app": {"type": "string", "description": "target application"},
                    "text": {"type": "string", "description": "for type action"},
                },
                "required": ["action"],
            },
        }

    # ------------------------------------------------------------------
    # Backend helpers
    # ------------------------------------------------------------------

    def _elements(self) -> tuple[list[dict[str, Any]], str]:
        return a11y.list_elements(ui_backend=self._ui_backend)

    def _snapshot(self) -> tuple[list[dict[str, Any]], list[ElementRef], str]:
        elements, error = self._elements()
        if error:
            return [], [], error
        now = self._clock()
        sid, refs = build_refs(elements, round(now * 1000))
        self._snapshots[sid] = (now, {ref.element_id: ref for ref in refs})
        self._snapshots = {
            key: value for key, value in self._snapshots.items()
            if now - value[0] <= self._snapshot_ttl
        }
        return elements, refs, ""

    def _locate(self, name: str, role: str | None = None) -> tuple[ElementRef | None, str]:
        elements, refs, error = self._snapshot()
        if error:
            return None, error
        matches = a11y.find_elements(elements, name=name, role=role)
        if not matches:
            return None, f"No element found with name containing '{name}'."
        exact = [element for element in matches if (element.get("name") or "").casefold() == name.casefold()]
        candidates = exact or matches
        if len(candidates) != 1:
            labels = [f"{e.get('name', '')} ({e.get('role', '')})" for e in candidates[:5]]
            return None, f"Ambiguous element name '{name}': {len(candidates)} matches: {labels}"
        index = elements.index(candidates[0])
        return refs[index], ""

    def _resolve_id(self, raw_id: Any) -> tuple[ElementRef | None, str]:
        element_id = str(raw_id or "")
        if not element_id:
            return None, "element_id from a recent find/list result is required"
        match = next(
            ((sid, snapshot) for sid, snapshot in self._snapshots.items() if element_id in snapshot[1]),
            None,
        )
        if match is None:
            return None, "element_id is unknown or expired; run find again"
        sid, (captured_at, refs) = match
        if self._clock() - captured_at > self._snapshot_ttl:
            self._snapshots.pop(sid, None)
            return None, "element_id is stale; run find again"
        ref = refs.get(element_id)
        if ref is None:
            return None, "element_id does not belong to this snapshot"

        current, error = self._elements()
        if error:
            return None, error
        matches = [
            element for element in current
            if str(element.get("path") or "") == ref.path
            and str(element.get("role") or "") == ref.role
            and str(element.get("name") or "") == ref.name
        ]
        if len(matches) != 1:
            return None, "element_id is no longer unique; run find again"
        current_bbox = {
            "x": matches[0].get("x"), "y": matches[0].get("y"),
            "width": matches[0].get("width"), "height": matches[0].get("height"),
        }
        if current_bbox != ref.bbox:
            return None, "Element geometry changed; run find again"
        return ref, ""

    def _resolved_center(self, element: ElementRef) -> tuple[int, int] | None:
        x, y = element.bbox.get("x"), element.bbox.get("y")
        w, h = element.bbox.get("width"), element.bbox.get("height")
        if None in (x, y, w, h):
            return None
        return int(x + w / 2), int(y + h / 2)

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def _list(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        app = kwargs.get("app")
        elements, refs, error = self._snapshot()
        if error:
            return {"error": error, "fallback": "Use inspect_screen with action='look' for visual grounding."}
        if app:
            needle = app.lower()
            pairs = [
                (element, ref) for element, ref in zip(elements, refs)
                if needle in (element.get("name") or "").lower()
                or needle in (element.get("path") or "").lower()
            ]
            elements = [element for element, _ in pairs]
            refs = [ref for _, ref in pairs]
        enriched = [
            {**element, "element_id": ref.element_id}
            for element, ref in zip(elements[:200], refs[:200])
        ]
        return {"elements": enriched, "count": len(elements), "backend": self._ui_backend}

    def _find(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        element_name = kwargs.get("element")
        if not element_name:
            return {"error": "element is required for find action"}
        role = kwargs.get("role")
        element, error = self._locate(element_name, role)
        if error:
            return {"error": error}
        return {
            "found": True,
            "element_id": element.element_id,
            "bbox": dict(element.bbox),
            "center": self._resolved_center(element),
        }

    def _click(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        element, error = self._resolve_id(kwargs.get("element_id"))
        if error:
            return {"error": error}
        center = self._resolved_center(element)
        if center is None:
            return {"error": f"Element '{element.name}' has no usable geometry to click."}
        x, y = center
        try:
            import pyautogui
        except ImportError:
            return {"error": "pyautogui not installed"}

        if kwargs.get("double"):
            pyautogui.doubleClick(x, y)
        else:
            pyautogui.click(x, y)
        return {
            "success": True,
            "name": element.name,
            "clicked_at": {"x": x, "y": y},
            "element": {"role": element.role, "name": element.name},
        }

    def _type_text(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        text = kwargs.get("text")
        if text is None:
            return {"error": "text is required for type action"}

        try:
            import pyautogui
        except ImportError:
            return {"error": "pyautogui not installed"}

        # Focus the element first by clicking its center.
        click_result = self._click(kwargs)
        if "error" in click_result:
            return click_result

        if kwargs.get("clear"):
            pyautogui.hotkey("ctrl", "a")
            pyautogui.press("delete")
        pyautogui.write(text, interval=0.01)
        if kwargs.get("enter"):
            pyautogui.press("enter")
        return {
            "success": True,
            "name": click_result.get("name", ""),
            "typed": text if len(text) <= 80 else text[:77] + "...",
            "clicked_at": click_result.get("clicked_at"),
        }

    def _screenshot_element(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        element, error = self._resolve_id(kwargs.get("element_id"))
        if error:
            return {"error": error}
        x, y = element.bbox.get("x"), element.bbox.get("y")
        w, h = element.bbox.get("width"), element.bbox.get("height")
        if None in (x, y, w, h):
            return {"error": f"Element '{element.name}' has no usable geometry to screenshot."}

        try:
            import mss
            from PIL import Image
        except ImportError:
            return {"error": "mss or Pillow not installed"}

        try:
            save_path = self._artifacts.allocate(
                prefix="ui-element",
                suffix=".png",
                requested=kwargs.get("save_path"),
            )
        except ValueError as exc:
            return {"error": str(exc)}
        try:
            with mss.mss() as sct:
                monitor = {"left": x, "top": y, "width": max(1, w), "height": max(1, h)}
                shot = sct.grab(monitor)
                img = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
                img.save(str(save_path))
            result: dict[str, Any] = {
                "success": True,
                "path": str(save_path),
                "artifact": image_artifact(save_path, "image/png"),
                "size": (w, h),
                "name": element.name,
            }
            if kwargs.get("inline"):
                from pc_assistant.vision.preprocess import image_block_from_file

                block = image_block_from_file(str(save_path))
                if block is not None:
                    block["width"] = w
                    block["height"] = h
                    result["image"] = block
            return result
        except Exception as e:
            return {"error": f"Failed to capture element: {e}"}
