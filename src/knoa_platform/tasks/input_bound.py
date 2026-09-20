"""Bound oversized Agent turn inputs via the ArtifactStore.

Tool outputs already spill to artifacts with a bounded preview
(``knoa_agent.runtime``) and ``web_fetch`` returns query-focused excerpts.
Task goals previously rode inline with no bound: a trigger payload or a
pasted goal goes straight into the first user message, which exhausts
small local-model context windows before the first LLM call
(``context_budget_exceeded``).

This module gives task execution the same spill-and-pointer treatment:
overlong input is stored as a session artifact and the turn receives a
bounded head/tail preview plus a ``read_artifact`` pointer, so the Agent
pulls detail on demand instead of drowning in it.
"""
from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

#: Goals at or below this size ride inline unchanged. Sized so the bounded
#: preview plus tool schemas still fit the smallest supported window
#: (16k local models: 16384 - 4096 completion reserve = 12288 budget,
#: tool schemas alone take ~6k).
TURN_INPUT_SPILL_THRESHOLD_CHARS = 8000
#: Bounded preview budget (split evenly head/tail) when spilling.
TURN_INPUT_PREVIEW_CHARS = 4000


def _head_tail_preview(text: str, budget: int) -> tuple[str, str, int]:
    half = max(100, budget // 2)
    head = text[:half]
    tail = text[-half:] if half > 0 else ""
    omitted = len(text) - len(head) - len(tail)
    return head, tail, max(0, omitted)


def bound_turn_input(
    text: str,
    *,
    artifacts: Any | None = None,
    session_id: str = "",
    label: str = "task-input",
) -> str:
    """Return a context-safe variant of ``text`` for use as turn input.

    Short inputs pass through unchanged. Overlong inputs spill the full
    text to a session artifact and return a bounded head/tail preview with
    a ``read_artifact`` pointer. The stored goal/record keeps the full
    text; only the model-facing input is bounded. Never raises: when no
    store is available the preview is returned with a truncation note.
    """
    if len(text) <= TURN_INPUT_SPILL_THRESHOLD_CHARS:
        return text
    head, tail, omitted = _head_tail_preview(text, TURN_INPUT_PREVIEW_CHARS)
    preview = f"{head}\n\n[... {omitted} chars omitted ...]\n\n{tail}"
    artifact_id: str | None = None
    if artifacts is not None and session_id:
        try:
            created = artifacts.create_generated_text(
                session_id,
                text,
                name=f"{label}.txt",
                retention="session",
            )
            artifact_id = str(created.get("artifact_id") or "") or None
        except Exception:  # noqa: BLE001 - bounding must never break execution
            logger.warning("Turn input spill to artifact failed", exc_info=True)
    if artifact_id:
        return (
            f"{preview}\n\n[Full input ({len(text)} chars) saved to artifact "
            f"'{artifact_id}'. Use read_artifact(artifact_id='{artifact_id}') "
            "to inspect specific sections.]"
        )
    return (
        f"{preview}\n\n[Input truncated to a bounded preview "
        f"({len(text)} chars total).]"
    )


def _one_line(text: str, limit: int) -> str:
    collapsed = " ".join(str(text).split())
    return collapsed if len(collapsed) <= limit else collapsed[:limit] + "..."


def summarize_trigger_payload(payload: dict[str, Any]) -> str:
    """Render a compact, human-readable summary of a trigger event payload.

    Generic across event sources: MCP resource snapshots get per-content
    mime/size/excerpt lines, anything else gets top-level keys plus a head
    excerpt. Used for bounded trigger goals so the Agent can route without
    the full snapshot inline.
    """
    if not isinstance(payload, dict) or not payload:
        return "(empty event payload)"
    lines: list[str] = []
    server_id = payload.get("server_id")
    resource_uri = payload.get("resource_uri")
    if isinstance(server_id, str) and server_id:
        lines.append(f"source: {server_id}")
    if isinstance(resource_uri, str) and resource_uri:
        lines.append(f"resource: {resource_uri}")
    name = payload.get("resource_name")
    if isinstance(name, str) and name:
        lines.append(f"name: {name}")
    contents = payload.get("contents")
    if isinstance(contents, list) and contents:
        total = 0
        for index, item in enumerate(contents):
            if not isinstance(item, dict):
                continue
            text = item.get("text") if isinstance(item.get("text"), str) else ""
            total += len(text)
            uri = item.get("uri") if isinstance(item.get("uri"), str) else f"content[{index}]"
            mime = item.get("mime_type") if isinstance(item.get("mime_type"), str) else "unknown"
            lines.append(f"- {uri} [{mime}]: {len(text)} chars")
            if text:
                lines.append(f"  excerpt: {_one_line(text, 500)}")
        lines.append(f"snapshot total: {total} chars across {len(contents)} content block(s)")
        return "\n".join(lines)
    try:
        rendered = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    except (TypeError, ValueError):
        rendered = str(payload)
    keys = ", ".join(sorted(str(key) for key in payload))
    lines.append(f"payload keys: {keys}")
    lines.append(f"payload size: {len(rendered)} chars")
    lines.append(f"excerpt: {_one_line(rendered, 500)}")
    return "\n".join(lines)
