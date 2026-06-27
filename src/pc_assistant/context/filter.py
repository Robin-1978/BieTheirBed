"""Pre-LLM input filter — trim stale assistant/tool content."""
from __future__ import annotations

import json
import logging
from typing import Any

from pc_assistant.context.tags import is_protected_history, normalize_message_content, unwrap_tool_result

logger = logging.getLogger(__name__)

# Trim content before the latest user turn (recent turn stays full fidelity)
_ASSISTANT_TRIM_AT = 400
_ASSISTANT_PREVIEW = 240
_TOOL_TRIM_AT = 800
_TOOL_PREVIEW = 300


def _preview(text: str, preview_chars: int, total_len: int) -> str:
    if len(text) <= preview_chars:
        return text
    cut = text[:preview_chars].rstrip()
    return f"{cut}\n...[trimmed from {total_len} chars]"


def trim_stale_content(messages: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    """Trim long assistant/tool messages before the latest user turn.

    Recent turn stays intact, older bulky content replaced with compact previews.
    """
    if not messages:
        return messages, 0

    last_user_idx = -1
    for i in range(len(messages) - 1, -1, -1):
        if messages[i].get("role") == "user":
            last_user_idx = i
            break
    if last_user_idx <= 0:
        return messages, 0

    out: list[dict[str, Any]] = []
    trimmed = 0
    for i, msg in enumerate(messages):
        if i >= last_user_idx:
            out.append(msg)
            continue

        role = msg.get("role")
        content = normalize_message_content(msg.get("content"))
        if not content:
            out.append(msg)
            continue

        if role == "user":
            if is_protected_history(content):
                out.append(msg)
                continue
            out.append(msg)
            continue

        if role == "assistant":
            if is_protected_history(content) or len(content) <= _ASSISTANT_TRIM_AT:
                out.append(msg)
                continue
            new_msg = dict(msg)
            new_msg["content"] = _preview(content, _ASSISTANT_PREVIEW, len(content))
            out.append(new_msg)
            trimmed += 1
            continue

        if role == "tool":
            if len(content) <= _TOOL_TRIM_AT:
                out.append(msg)
                continue
            try:
                data = json.loads(unwrap_tool_result(content))
                summary = data.get("summary") or data.get("error") or ""
                if summary and len(str(summary)) <= _TOOL_PREVIEW:
                    out.append(msg)
                    continue
            except (json.JSONDecodeError, TypeError):
                pass
            new_msg = dict(msg)
            new_msg["content"] = _preview(content, _TOOL_PREVIEW, len(content))
            out.append(new_msg)
            trimmed += 1
            continue

        out.append(msg)

    if trimmed:
        logger.info("[ContextFilter] Trimmed %d stale message(s) before latest user turn", trimmed)
    return out, trimmed