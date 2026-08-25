"""Cross-platform accessibility tree access for semantic GUI automation."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Callable

from knoa_platform.platform_ import get_platform


@dataclass(frozen=True)
class AccessibleElement:
    role: str
    name: str
    state: dict[str, bool]
    bounds: dict[str, int] | None
    children_count: int
    path: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _normalize_state(raw: Any) -> dict[str, bool]:
    if isinstance(raw, dict):
        return {str(key): bool(value) for key, value in raw.items()}
    if isinstance(raw, (list, tuple, set)):
        return {str(item): True for item in raw}
    return {}


def _resolve_backend(backend: str) -> str:
    normalized = (backend or "auto").strip().lower()
    if normalized in {"auto", "atspi", "uia", "none"}:
        return normalized
    raise ValueError(f"Unsupported ui_backend '{backend}'. Use auto, atspi, uia, or none.")


def _backend_factory(backend: str) -> Callable[[], Any]:
    resolved = _resolve_backend(backend)
    if resolved == "none":
        return _unsupported_backend("ui_backend is disabled")

    plat = get_platform()
    if resolved == "auto":
        if plat == "linux":
            resolved = "atspi"
        elif plat == "windows":
            resolved = "uia"
        else:
            return _unsupported_backend(f"Accessibility is not supported on {plat}")

    if resolved == "atspi":
        if plat != "linux":
            return _unsupported_backend("AT-SPI backend is only available on Linux")
        return _AtspiBackend
    if resolved == "uia":
        if plat != "windows":
            return _unsupported_backend("UIA backend is only available on Windows")
        return _UiaBackend
    return _unsupported_backend(f"Unsupported backend '{resolved}'")


def _unsupported_backend(message: str) -> Callable[[], Any]:
    class _DisabledBackend:
        def __init__(self) -> None:
            self.error = message

        def get_accessible_tree(self, window_name: str | None = None) -> list[AccessibleElement]:
            raise RuntimeError(self.error)

        def perform_action(
            self,
            element_path: str,
            action: str,
            value: str | None = None,
        ) -> dict[str, Any]:
            return {"error": self.error}

    return _DisabledBackend


class _AtspiBackend:
    def __init__(self) -> None:
        try:
            import pyatspi  # type: ignore[import-untyped]
        except ImportError as exc:
            raise RuntimeError(
                "pyatspi is not installed. Install python3-pyatspi or pyatspi on Linux."
            ) from exc
        self._pyatspi = pyatspi

    def get_accessible_tree(self, window_name: str | None = None) -> list[AccessibleElement]:
        desktop = self._pyatspi.Registry.getDesktop(0)
        roots: list[tuple[int, Any]] = []
        for index in range(desktop.childCount):
            child = desktop.getChildAtIndex(index)
            if child is None:
                continue
            if window_name and not self._matches_window(child, window_name):
                continue
            roots.append((index, child))
        if window_name and not roots:
            raise LookupError(f"Window not found: {window_name}")
        if not roots:
            active = self._active_application()
            if active is not None:
                roots = [
                    (index, child)
                    for index in range(desktop.childCount)
                    for child in (desktop.getChildAtIndex(index),)
                    if child is active
                ]
        elements: list[AccessibleElement] = []
        for root_index, root in roots:
            prefix = str(root_index)
            self._walk(root, prefix, elements, max_depth=8)
        return elements

    def perform_action(
        self,
        element_path: str,
        action: str,
        value: str | None = None,
    ) -> dict[str, Any]:
        element = self._resolve_path(element_path)
        if element is None:
            return {"error": f"Element not found for path: {element_path}"}
        try:
            if action == "click":
                return self._click_element(element)
            if action == "fill":
                if value is None:
                    return {"error": "value is required for fill action"}
                return self._fill_element(element, value)
            if action == "select":
                if value is None:
                    return {"error": "value is required for select action"}
                return self._select_element(element, value)
            if action == "focus":
                return self._focus_element(element)
            return {"error": f"Unsupported action: {action}"}
        except Exception as exc:
            return {"error": f"Accessibility action failed: {exc}"}

    def _active_application(self) -> Any | None:
        try:
            import pyatspi  # type: ignore[import-untyped]

            return pyatspi.Registry.getActiveDescendant(
                pyatspi.Registry.getDesktop(0)
            )
        except Exception:
            return None

    @staticmethod
    def _matches_window(node: Any, window_name: str) -> bool:
        needle = window_name.casefold()
        name = str(getattr(node, "name", "") or "").casefold()
        if needle in name:
            return True
        role = str(getattr(node, "roleName", "") or "").casefold()
        return role == "frame" and needle in name

    def _walk(
        self,
        node: Any,
        path: str,
        output: list[AccessibleElement],
        *,
        max_depth: int,
        depth: int = 0,
    ) -> None:
        if depth > max_depth:
            return
        role = str(getattr(node, "roleName", "") or "unknown")
        name = str(getattr(node, "name", "") or "")
        state = _normalize_state(getattr(getattr(node, "state", None), "getStates", lambda: [])())
        bounds = self._bounds(node)
        child_count = int(getattr(node, "childCount", 0) or 0)
        if self._is_interactive(role, name, state, child_count):
            output.append(
                AccessibleElement(
                    role=role,
                    name=name,
                    state=state,
                    bounds=bounds,
                    children_count=child_count,
                    path=path,
                )
            )
        for index in range(child_count):
            child = node.getChildAtIndex(index)
            if child is None:
                continue
            self._walk(child, f"{path}/{index}", output, max_depth=max_depth, depth=depth + 1)

    @staticmethod
    def _is_interactive(
        role: str,
        name: str,
        state: dict[str, bool],
        child_count: int,
    ) -> bool:
        interactive_roles = {
            "push button",
            "toggle button",
            "check box",
            "radio button",
            "text",
            "entry",
            "password text",
            "combo box",
            "menu item",
            "list item",
            "link",
            "spin button",
            "slider",
            "tree item",
            "table cell",
        }
        if role.lower() in interactive_roles:
            return True
        return bool(name) and child_count == 0 and role.lower() not in {"panel", "filler"}

    @staticmethod
    def _bounds(node: Any) -> dict[str, int] | None:
        try:
            component = node.queryComponent()
        except Exception:
            return None
        try:
            extents = component.getExtents(0)
        except Exception:
            return None
        return {
            "x": int(extents.x),
            "y": int(extents.y),
            "width": int(extents.width),
            "height": int(extents.height),
        }

    def _resolve_path(self, element_path: str) -> Any | None:
        desktop = self._pyatspi.Registry.getDesktop(0)
        parts = [part for part in str(element_path).split("/") if part != ""]
        if not parts:
            return None
        try:
            root_index = int(parts[0])
        except ValueError:
            return self._find_by_name(desktop, element_path)
        if root_index >= desktop.childCount:
            return None
        node = desktop.getChildAtIndex(root_index)
        for part in parts[1:]:
            if node is None:
                return None
            try:
                index = int(part)
            except ValueError:
                return self._find_by_name(node, part)
            node = node.getChildAtIndex(index)
        return node

    def _find_by_name(self, root: Any, needle: str) -> Any | None:
        target = needle.casefold()
        matches: list[Any] = []

        def walk(node: Any) -> None:
            name = str(getattr(node, "name", "") or "").casefold()
            if target and (name == target or target in name):
                matches.append(node)
            for index in range(int(getattr(node, "childCount", 0) or 0)):
                child = node.getChildAtIndex(index)
                if child is not None:
                    walk(child)

        walk(root)
        if len(matches) == 1:
            return matches[0]
        exact = [
            node
            for node in matches
            if str(getattr(node, "name", "") or "").casefold() == target
        ]
        return exact[0] if len(exact) == 1 else None

    def _click_element(self, element: Any) -> dict[str, Any]:
        try:
            action = element.queryAction()
            for index in range(action.nActions):
                if "click" in action.getActionName(index).casefold():
                    action.doAction(index)
                    return {"success": True, "action": "click"}
        except Exception:
            pass
        bounds = self._bounds(element)
        if bounds is None:
            return {"error": "Element has no clickable action or bounds"}
        return self._click_bounds(bounds)

    def _fill_element(self, element: Any, value: str) -> dict[str, Any]:
        focus_result = self._focus_element(element)
        if focus_result.get("error"):
            return focus_result
        try:
            import pyautogui
            import pyperclip

            from knoa_platform.platform_ import get_platform

            pyautogui.hotkey("ctrl", "a")
            pyperclip.copy(value)
            if get_platform() == "macos":
                pyautogui.hotkey("command", "v")
            else:
                pyautogui.hotkey("ctrl", "v")
            return {"success": True, "action": "fill", "characters": len(value)}
        except ImportError:
            return {"error": "pyautogui and pyperclip are required for text entry"}

    def _select_element(self, element: Any, value: str) -> dict[str, Any]:
        opened = self._click_element(element)
        if opened.get("error"):
            return opened
        option = self._find_by_name(element, value)
        if option is None:
            desktop = self._pyatspi.Registry.getDesktop(0)
            option = self._find_by_name(desktop, value)
        if option is None:
            return {"error": f"Option not found: {value}"}
        return self._click_element(option)

    def _focus_element(self, element: Any) -> dict[str, Any]:
        try:
            element.grabFocus()
            return {"success": True, "action": "focus"}
        except Exception as exc:
            clicked = self._click_element(element)
            if clicked.get("success"):
                return clicked
            return {"error": f"Failed to focus element: {exc}"}

    @staticmethod
    def _click_bounds(bounds: dict[str, int]) -> dict[str, Any]:
        try:
            import pyautogui
        except ImportError:
            return {"error": "pyautogui is required for coordinate clicks"}
        x = bounds["x"] + bounds["width"] // 2
        y = bounds["y"] + bounds["height"] // 2
        pyautogui.click(x, y)
        return {"success": True, "action": "click", "x": x, "y": y}


class _UiaBackend:
    def __init__(self) -> None:
        try:
            from pywinauto import Desktop  # type: ignore[import-untyped]
        except ImportError as exc:
            raise RuntimeError(
                "pywinauto is not installed. Run: pip install pywinauto"
            ) from exc
        self._Desktop = Desktop

    def get_accessible_tree(self, window_name: str | None = None) -> list[AccessibleElement]:
        desktop = self._Desktop(backend="uia")
        windows = desktop.windows()
        selected = list(enumerate(windows))
        if window_name:
            needle = window_name.casefold()
            selected = [
                (index, window)
                for index, window in enumerate(windows)
                if needle in str(window.window_text()).casefold()
            ]
            if not selected:
                raise LookupError(f"Window not found: {window_name}")
        elements: list[AccessibleElement] = []
        for root_index, window in selected[:1]:
            wrapper = window.wrapper_object()
            self._walk(wrapper, str(root_index), elements, max_depth=8)
        return elements

    def perform_action(
        self,
        element_path: str,
        action: str,
        value: str | None = None,
    ) -> dict[str, Any]:
        desktop = self._Desktop(backend="uia")
        element = self._resolve_path(desktop, element_path)
        if element is None:
            return {"error": f"Element not found for path: {element_path}"}
        try:
            if action == "click":
                element.click_input()
                return {"success": True, "action": "click"}
            if action == "fill":
                if value is None:
                    return {"error": "value is required for fill action"}
                element.set_focus()
                element.set_edit_text(value)
                return {"success": True, "action": "fill", "characters": len(value)}
            if action == "select":
                if value is None:
                    return {"error": "value is required for select action"}
                element.select(value)
                return {"success": True, "action": "select"}
            if action == "focus":
                element.set_focus()
                return {"success": True, "action": "focus"}
            return {"error": f"Unsupported action: {action}"}
        except Exception as exc:
            return {"error": f"Accessibility action failed: {exc}"}

    def _walk(
        self,
        node: Any,
        path: str,
        output: list[AccessibleElement],
        *,
        max_depth: int,
        depth: int = 0,
    ) -> None:
        if depth > max_depth:
            return
        try:
            info = node.element_info
            role = str(getattr(info, "control_type", "") or "unknown")
            name = str(getattr(info, "name", "") or "")
            rectangle = info.rectangle
            bounds = {
                "x": int(rectangle.left),
                "y": int(rectangle.top),
                "width": int(rectangle.width()),
                "height": int(rectangle.height()),
            }
            child_count = len(node.children())
            state = {
                "enabled": bool(getattr(info, "enabled", True)),
                "visible": bool(getattr(info, "visible", True)),
            }
            if self._is_interactive(role, name):
                output.append(
                    AccessibleElement(
                        role=role,
                        name=name,
                        state=state,
                        bounds=bounds,
                        children_count=child_count,
                        path=path,
                    )
                )
            for index, child in enumerate(node.children()):
                self._walk(child, f"{path}/{index}", output, max_depth=max_depth, depth=depth + 1)
        except Exception:
            return

    @staticmethod
    def _is_interactive(role: str, name: str) -> bool:
        lowered = role.casefold()
        if any(token in lowered for token in ("button", "edit", "combo", "check", "radio", "listitem", "menuitem", "hyperlink")):
            return True
        return bool(name)

    def _resolve_path(self, desktop: Any, element_path: str) -> Any | None:
        parts = [part for part in str(element_path).split("/") if part != ""]
        if not parts:
            return None
        try:
            root_index = int(parts[0])
        except ValueError:
            return self._find_by_name(desktop, element_path)
        windows = desktop.windows()
        if root_index >= len(windows):
            return None
        node = windows[root_index].wrapper_object()
        for part in parts[1:]:
            try:
                index = int(part)
            except ValueError:
                return self._find_by_name(node, part)
            children = node.children()
            if index >= len(children):
                return None
            node = children[index]
        return node

    def _find_by_name(self, root: Any, needle: str) -> Any | None:
        target = needle.casefold()
        matches: list[Any] = []

        def walk(node: Any) -> None:
            try:
                name = str(getattr(node.element_info, "name", "") or "").casefold()
            except Exception:
                name = ""
            if target and (name == target or target in name):
                matches.append(node)
            for child in node.children():
                walk(child)

        walk(root if hasattr(root, "children") else root.wrapper_object())
        if len(matches) == 1:
            return matches[0]
        exact = [
            node
            for node in matches
            if str(getattr(node.element_info, "name", "") or "").casefold() == target
        ]
        return exact[0] if len(exact) == 1 else None


class A11yService:
    def __init__(self, backend: str = "auto") -> None:
        self._backend_name = backend
        self._backend: Any | None = None
        self._backend_error: str | None = None

    def _ensure_backend(self) -> Any:
        if self._backend is not None:
            return self._backend
        if self._backend_error is not None:
            raise RuntimeError(self._backend_error)
        try:
            backend_cls = _backend_factory(self._backend_name)
            self._backend = backend_cls()
            return self._backend
        except RuntimeError as exc:
            self._backend_error = str(exc)
            raise

    def get_accessible_tree(self, window_name: str | None = None) -> list[AccessibleElement]:
        backend = self._ensure_backend()
        return backend.get_accessible_tree(window_name=window_name)

    def perform_action(
        self,
        element_path: str,
        action: str,
        value: str | None = None,
    ) -> dict[str, Any]:
        try:
            backend = self._ensure_backend()
        except RuntimeError as exc:
            return {"error": str(exc)}
        return backend.perform_action(element_path, action, value=value)


def get_accessible_tree(
    window_name: str | None = None,
    *,
    backend: str = "auto",
) -> list[AccessibleElement]:
    return A11yService(backend=backend).get_accessible_tree(window_name=window_name)


def perform_action(
    element_path: str,
    action: str,
    value: str | None = None,
    *,
    backend: str = "auto",
) -> dict[str, Any]:
    return A11yService(backend=backend).perform_action(element_path, action, value=value)
