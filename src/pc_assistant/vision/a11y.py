"""Cross-platform accessibility (a11y) backend for the semantic UI layer.

The semantic layer (``tools/ui.py``) prefers element names + bounding boxes over
blind pixel guessing. This module walks the OS accessibility tree and normalises
it into flat records:

    {"name", "role", "x", "y", "width", "height", "depth", "path"}

Backends are optional-dependency aware and fail closed: if the platform's a11y
library (pyatspi / pywinauto / pyobjc) is missing the tool reports a clear
message instead of crashing, and the visual layer (``screen``) becomes the
fallback.
"""
from __future__ import annotations

from collections import deque
from typing import Any, Iterator

from pc_assistant.platform_ import get_platform

DEFAULT_MAX_ELEMENTS = 300
MAX_DEPTH = 40


def node_name(node: Any) -> str:
    name = getattr(node, "name", None)
    return str(name) if name else ""


def node_role(node: Any) -> str:
    get_role_name = getattr(node, "getRoleName", None)
    if callable(get_role_name):
        try:
            return str(get_role_name())
        except Exception:
            pass
    role = getattr(node, "role", None)
    if role is None:
        return ""
    if isinstance(role, str):
        return role
    try:
        import pyatspi

        return str(pyatspi.Role.getName(role))
    except Exception:
        return str(role)


def read_bbox(node: Any) -> tuple[int, int, int, int] | None:
    """Return (x, y, width, height) via duck-typed a11y geometry accessors."""
    get_extents = getattr(node, "getExtents", None)
    if callable(get_extents):
        try:
            r = get_extents(0)
            if r is not None and len(r) >= 4:
                return int(r[0]), int(r[1]), int(r[2]), int(r[3])
        except Exception:
            pass
    query_component = getattr(node, "queryComponent", None)
    if callable(query_component):
        try:
            import pyatspi

            r = query_component().getExtents(pyatspi.DESKTOP_COORDS)
            return int(r.x), int(r.y), int(r.width), int(r.height)
        except Exception:
            pass
    box = getattr(node, "box", None) or getattr(node, "bbox", None) or getattr(node, "rectangle", None)
    if box is not None:
        try:
            return int(box.left), int(box.top), int(box.width), int(box.height)
        except Exception:
            pass
    rect = getattr(node, "rectangle", None)
    if callable(rect):
        try:
            r = rect()
            return int(r.left), int(r.top), int(r.width), int(r.height)
        except Exception:
            pass
    pos = getattr(node, "get_position", None)
    size = getattr(node, "get_size", None)
    if callable(pos) and callable(size):
        try:
            (x, y), (w, h) = pos(), size()
            return int(x), int(y), int(w), int(h)
        except Exception:
            pass
    return None


def _iter_children(node: Any) -> list[Any]:
    children = getattr(node, "children", None)
    if callable(children):
        try:
            return list(children())
        except Exception:
            return []
    get_count = getattr(node, "getChildCount", None)
    if callable(get_count):
        try:
            count = int(get_count())
        except Exception:
            count = 0
        out: list[Any] = []
        for i in range(count):
            child = None
            try:
                child = node[i]
            except Exception:
                try:
                    child = node.getChildAtIndex(i)
                except Exception:
                    child = None
            if child is not None:
                out.append(child)
        return out
    child_count = getattr(node, "childCount", None)
    if child_count is not None:
        try:
            count = int(child_count)
        except Exception:
            count = 0
        out = []
        for i in range(count):
            try:
                out.append(node[i])
            except Exception:
                try:
                    out.append(node.getChildAtIndex(i))
                except Exception:
                    pass
        return out
    if isinstance(children, (list, tuple)):
        return list(children)
    return []


def walk_forest_breadth_first(
    roots: list[Any],
    *,
    max_elements: int = DEFAULT_MAX_ELEMENTS,
) -> list[dict[str, Any]]:
    """Walk all applications fairly so one large shell tree cannot starve others."""
    # Keep one breadth-first frontier per application and rotate across them.
    # A single application's unusually broad level must not starve descendants
    # of another application under the global element budget.
    forests = deque(
        deque([(root, 0, ())]) for root in roots if root is not None
    )
    elements: list[dict[str, Any]] = []
    while forests and len(elements) < max_elements:
        frontier = forests.popleft()
        node, depth, path = frontier.popleft()
        name = node_name(node)
        role = node_role(node)
        current_path = path + ((name or role or "?"),)
        element: dict[str, Any] = {
            "name": name,
            "role": role,
            "x": None,
            "y": None,
            "width": None,
            "height": None,
            "depth": depth,
            "path": " > ".join(current_path),
        }
        bbox = read_bbox(node)
        if bbox is not None:
            element["x"], element["y"], element["width"], element["height"] = bbox
        elements.append(element)
        if depth < MAX_DEPTH:
            frontier.extend(
                (child, depth + 1, current_path) for child in _iter_children(node)
            )
        if frontier:
            forests.append(frontier)
    return elements


def walk(
    node: Any,
    *,
    depth: int = 0,
    path: tuple[str, ...] = (),
    max_elements: int = DEFAULT_MAX_ELEMENTS,
) -> Iterator[dict[str, Any]]:
    """Pre-order DFS over an a11y tree, yielding flattened element records."""
    if node is None or depth > MAX_DEPTH:
        return
    name = node_name(node)
    role = node_role(node)
    element: dict[str, Any] = {
        "name": name,
        "role": role,
        "x": None,
        "y": None,
        "width": None,
        "height": None,
        "depth": depth,
        "path": " > ".join(list(path) + ([name] if name else [role or "?"])),
    }
    bbox = read_bbox(node)
    if bbox is not None:
        element["x"], element["y"], element["width"], element["height"] = bbox
    yield element

    if len(path) >= MAX_DEPTH:
        return
    for child in _iter_children(node):
        yield from walk(
            child,
            depth=depth + 1,
            path=path + ((name,) if name else (role or "?",)),
            max_elements=max_elements,
        )


def find_elements(
    elements: list[dict[str, Any]],
    *,
    name: str | None = None,
    role: str | None = None,
) -> list[dict[str, Any]]:
    """Filter flat element records by (case-insensitive, substring) name/role."""
    matches = elements
    if name:
        needle = name.lower()
        matches = [e for e in matches if needle in (e.get("name") or "").lower()]
    if role:
        needle = role.lower()
        matches = [e for e in matches if needle in (e.get("role") or "").lower()]
    return matches


# ---------------------------------------------------------------------------
# Platform backends
# ---------------------------------------------------------------------------


class LinuxAtspiBackend:
    name = "atspi"
    platform = "linux"

    def available(self) -> bool:
        try:
            import pyatspi  # noqa: F401
            return True
        except ImportError:
            return False

    def root_nodes(self) -> list[Any]:
        import pyatspi

        roots = []
        i = 0
        while i < 32:
            try:
                roots.append(pyatspi.Registry.getDesktop(i))
            except Exception:
                break
            i += 1
        return roots


class WindowsUIABackend:
    name = "pywinauto"
    platform = "windows"

    def available(self) -> bool:
        try:
            import pywinauto  # noqa: F401
            return True
        except ImportError:
            return False

    def root_nodes(self) -> list[Any]:
        from pywinauto import Desktop

        return list(Desktop(backend="uia").windows())


class MacOSAXBackend:
    name = "ax"
    platform = "macos"

    def available(self) -> bool:
        try:
            import ApplicationServices  # noqa: F401
            return True
        except ImportError:
            return False

    def root_nodes(self) -> list[Any]:
        from ApplicationServices import AXUIElementCreateSystemWide

        return [AXUIElementCreateSystemWide()]


def get_backend(platform: str | None = None, ui_backend: str = "auto"):
    """Return the first available backend for the platform, or None."""
    if platform is None:
        platform = get_platform()
    candidates: list = []
    if platform == "linux":
        candidates = [LinuxAtspiBackend()]
    elif platform == "windows":
        candidates = [WindowsUIABackend()]
    elif platform == "macos":
        candidates = [MacOSAXBackend()]

    if ui_backend not in ("auto", "") and candidates:
        candidates = [b for b in candidates if b.name == ui_backend]
    for backend in candidates:
        if backend.available():
            return backend
    return None


def list_elements(
    platform: str | None = None,
    ui_backend: str = "auto",
    max_elements: int = DEFAULT_MAX_ELEMENTS,
) -> tuple[list[dict[str, Any]], str]:
    """Flatten the platform accessibility tree.

    Returns ``(elements, error)``; ``error`` is non-empty when the backend is
    unavailable or the tree could not be read.
    """
    backend = get_backend(platform, ui_backend)
    if backend is None:
        return [], (
            "Accessibility backend unavailable. Install pyatspi (Linux), "
            "pywinauto (Windows) or pyobjc (macOS) to use the semantic UI layer."
        )
    try:
        elements = walk_forest_breadth_first(
            backend.root_nodes(),
            max_elements=max_elements,
        )
        return elements, ""
    except Exception as e:
        return [], f"Failed to read accessibility tree: {e}"
