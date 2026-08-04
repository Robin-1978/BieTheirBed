"""Snapshot-bound semantic GUI target references."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ElementRef:
    snapshot_id: str
    element_id: str
    name: str
    role: str
    path: str
    bbox: dict[str, int | None]

    def to_dict(self) -> dict[str, Any]:
        return {
            "snapshot_id": self.snapshot_id,
            "element_id": self.element_id,
            "name": self.name,
            "role": self.role,
            "path": self.path,
            "bbox": dict(self.bbox),
        }


def snapshot_id(elements: list[dict[str, Any]], captured_ms: int) -> str:
    payload = json.dumps(elements, sort_keys=True, ensure_ascii=False, default=str)
    digest = hashlib.sha256(payload.encode()).hexdigest()[:12]
    return f"{captured_ms:x}-{digest}"


def build_refs(elements: list[dict[str, Any]], captured_ms: int) -> tuple[str, list[ElementRef]]:
    sid = snapshot_id(elements, captured_ms)
    refs: list[ElementRef] = []
    for index, element in enumerate(elements):
        identity = f"{sid}:{index}:{element.get('path', '')}:{element.get('role', '')}"
        element_id = hashlib.sha256(identity.encode()).hexdigest()[:16]
        refs.append(ElementRef(
            snapshot_id=sid,
            element_id=element_id,
            name=str(element.get("name") or ""),
            role=str(element.get("role") or ""),
            path=str(element.get("path") or ""),
            bbox={
                "x": element.get("x"),
                "y": element.get("y"),
                "width": element.get("width"),
                "height": element.get("height"),
            },
        ))
    return sid, refs
