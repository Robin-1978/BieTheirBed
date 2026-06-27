"""History compression — turn-level tool result summarization."""
from __future__ import annotations

import json
from typing import Any

from pc_assistant.context.tags import (
    format_compacted_history,
    is_compacted_history,
    is_protected_history,
    normalize_message_content,
    PROTECTED_HISTORY_MAX,
    truncate_text,
    unwrap_tool_result,
)

COMPACTED_HISTORY_ACK = "[Context acknowledged - summary is lossy, not verbatim history]"


def is_ephemeral_context_message(msg: dict[str, Any]) -> bool:
    """Runtime-only scaffold from build_compacted_pair — must not persist."""
    role = msg.get("role", "")
    content = normalize_message_content(msg.get("content") or "").strip()
    if role == "user" and is_compacted_history(content):
        return True
    if role == "assistant" and content == COMPACTED_HISTORY_ACK:
        return True
    return False


def strip_ephemeral(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop runtime-only scaffold messages."""
    return [m for m in messages if not is_ephemeral_context_message(m)]


def format_tool_call_line(tc: dict[str, Any]) -> tuple[str, str]:
    func = tc.get("function", {})
    name = func.get("name", "unknown")
    try:
        args = json.loads(func.get("arguments", "{}")) if isinstance(func.get("arguments"), str) else func.get("arguments", {})
        key_args = {k: v for k, v in list(args.items())[:2] if v}
        args_str = f"({json.dumps(key_args, ensure_ascii=False)})" if key_args else ""
    except (json.JSONDecodeError, TypeError):
        args_str = ""
    return f"Called {name}{args_str}", name


# Per-tool key fields to preserve in compacted summaries
_TOOL_RESULT_KEYS: dict[str, tuple[str, ...]] = {
    "shell": ("stdout", "stderr", "exit_code"),
    "filesystem": ("action", "path", "bytes"),
    "web": ("url", "status"),
    "weather": ("location", "temperature", "condition"),
    "exchange": ("from", "to", "rate"),
    "system": ("action",),
    "application": ("action",),
    "clipboard": ("action",),
    "scheduler": ("action", "job_name"),
    "memory": ("action", "key"),
}


def format_tool_result_line(content: str, *, tool_name: str = "") -> str:
    try:
        data = json.loads(unwrap_tool_result(content))
        if not isinstance(data, dict):
            return f"-> {str(content)[:60]}" if content else "-> ok"
        if "error" in data:
            return f"-> error: {str(data['error'])[:60]}"
        # Extract preserved fields
        keys = _TOOL_RESULT_KEYS.get(tool_name, ())
        parts = []
        for key in keys:
            val = data.get(key)
            if val not in (None, "", [], {}):
                s = str(val)[:60]
                parts.append(f"{key}={s}")
        if "summary" in data:
            return f"-> {data['summary']}"
        if parts:
            return "-> " + " | ".join(parts)
        return "-> ok"
    except (json.JSONDecodeError, TypeError):
        return f"-> {str(content)[:60]}" if content else "-> ok"


def summarize_tool_turn(turn: list[dict[str, Any]]) -> list[str]:
    """Extract fact lines from a turn that contains tool interactions."""
    facts: list[str] = []
    for i, msg in enumerate(turn):
        if msg.get("tool_calls"):
            for tc in msg["tool_calls"]:
                line, tool_name = format_tool_call_line(tc)
                for j in range(i + 1, min(i + 4, len(turn))):
                    nxt = turn[j]
                    if nxt.get("role") == "tool" and nxt.get("tool_call_id") == tc.get("id"):
                        line += " " + format_tool_result_line(
                            str(nxt.get("content") or ""),
                            tool_name=tool_name,
                        )
                        break
                facts.append(line)
    return facts


def build_compacted_pair(
    facts: list[str],
    *,
    covered_messages: int,
    keep_recent: int,
    source: str,
) -> list[dict[str, Any]]:
    if not facts:
        return []
    summary = format_compacted_history(
        facts,
        covered_messages=covered_messages,
        keep_recent=keep_recent,
        source=source,
    )
    return [
        {"role": "user", "content": summary},
        {"role": "assistant", "content": COMPACTED_HISTORY_ACK},
    ]


def compress_message_list(
    messages: list[dict[str, Any]],
    *,
    keep_recent: int,
    source: str = "memory_compress",
) -> list[dict[str, Any]]:
    """Linear compression used by ConversationStore.trim."""
    if len(messages) <= keep_recent:
        return messages

    old_messages = messages[:-keep_recent]
    recent_messages = messages[-keep_recent:]
    facts: list[str] = []
    i = 0
    while i < len(old_messages):
        msg = old_messages[i]
        role = msg.get("role", "")
        content = normalize_message_content(msg.get("content") or "")

        if is_ephemeral_context_message(msg):
            i += 1
            continue

        if role == "assistant" and msg.get("tool_calls"):
            facts.extend(summarize_tool_turn(old_messages[i:i + 4]))
            i += 1
            continue

        if role == "tool":
            i += 1
            continue

        if role == "user" and content:
            if is_protected_history(content):
                facts.append(f"User: {truncate_text(content, PROTECTED_HISTORY_MAX)}")
            else:
                facts.append(f"User: {content[:120]}")
        elif role == "assistant" and content and len(content) > 10:
            if is_protected_history(content):
                facts.append(f"Assistant: {truncate_text(content, PROTECTED_HISTORY_MAX)}")
            else:
                facts.append(f"Assistant: {content.split(chr(10))[0][:120]}")

        i += 1

    if not facts:
        return recent_messages

    compressed = build_compacted_pair(
        facts[-15:],
        covered_messages=len(old_messages),
        keep_recent=keep_recent,
        source=source,
    )
    compressed.extend(recent_messages)
    return compressed


def compact_dialogue_turn(
    turn: list[dict[str, Any]],
    *,
    keep_recent_turns: int,
    source: str = "context_edit",
) -> list[dict[str, Any]]:
    """Compress one dialogue turn for context assembly."""
    if any(is_ephemeral_context_message(m) for m in turn):
        return turn

    has_tool_interaction = any(m.get("role") == "tool" or m.get("tool_calls") for m in turn)
    if not has_tool_interaction:
        return turn

    user_msgs = [m for m in turn if m.get("role") == "user"]
    tool_summary_parts = summarize_tool_turn(turn)

    out: list[dict[str, Any]] = []
    out.extend(user_msgs)
    out.extend(
        build_compacted_pair(
            tool_summary_parts,
            covered_messages=len(turn),
            keep_recent=keep_recent_turns,
            source=source,
        ),
    )
    return out