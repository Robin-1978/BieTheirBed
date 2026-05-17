from __future__ import annotations

from typing import Any


def _estimate_tokens(text: str) -> int:
    if not text:
        return 0
    cjk = sum(1 for c in text if (
        '\u4e00' <= c <= '\u9fff'
        or '\u3400' <= c <= '\u4dbf'
        or '\U00020000' <= c <= '\U0002a6df'
        or '\u3040' <= c <= '\u309f'
        or '\u30a0' <= c <= '\u30ff'
        or '\uac00' <= c <= '\ud7af'
    ))
    non_cjk = len(text) - cjk
    return max(1, cjk + non_cjk // 4)


def _truncate_tool_output(msg: dict[str, Any], max_chars: int) -> dict[str, Any]:
    content = msg.get("content", "")
    if len(content) > max_chars:
        truncated = content[:max_chars] + f"\n... [truncated, {len(content) - max_chars} chars omitted]"
        msg = {**msg, "content": truncated}
    return msg


def _group_tool_pairs(messages: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    groups: list[list[dict[str, Any]]] = []
    i = 0
    while i < len(messages):
        msg = messages[i]
        if msg.get("role") == "assistant" and msg.get("delta_tool_calls"):
            group: list[dict[str, Any]] = [msg]
            tool_call_ids = {
                tc.get("id") for tc in msg.get("delta_tool_calls", []) if tc.get("id")
            }
            j = i + 1
            while j < len(messages) and messages[j].get("role") == "tool":
                if messages[j].get("tool_call_id") in tool_call_ids:
                    group.append(messages[j])
                else:
                    break
                j += 1
            groups.append(group)
            i = j
        else:
            groups.append([msg])
            i += 1
    return groups


def truncate_messages(
    messages: list[dict[str, Any]],
    budget: int = 4096,
    max_tool_output_chars: int = 3000,
) -> list[dict[str, Any]]:
    if not messages:
        return []

    processed: list[dict[str, Any]] = []
    for msg in messages:
        if msg.get("role") == "tool":
            msg = _truncate_tool_output(msg, max_tool_output_chars)
        processed.append(msg)

    system_msgs: list[dict[str, Any]] = []
    conversation_msgs: list[dict[str, Any]] = []
    for msg in processed:
        if msg.get("role") == "system":
            system_msgs.append(msg)
        else:
            conversation_msgs.append(msg)

    system_cost = sum(_estimate_tokens(m.get("content", "")) for m in system_msgs)
    remaining = budget - system_cost
    if remaining <= 0:
        return system_msgs

    groups = _group_tool_pairs(conversation_msgs)

    selected_groups: list[list[dict[str, Any]]] = []
    total = 0
    for group in reversed(groups):
        group_cost = sum(_estimate_tokens(m.get("content", "")) for m in group)
        if total + group_cost > remaining:
            break
        selected_groups.append(group)
        total += group_cost

    selected_groups.reverse()
    selected: list[dict[str, Any]] = []
    for group in selected_groups:
        selected.extend(group)

    dropped_groups = groups[: len(groups) - len(selected_groups)]
    if dropped_groups:
        summary_parts: list[str] = []
        for group in dropped_groups:
            for msg in group:
                snippet = msg.get("content", "")[:200]
                summary_parts.append(f"[{msg.get('role', 'unknown')}] {snippet}")
        summary = "Summary of earlier messages:\n" + "\n".join(summary_parts)
        summary_msg: dict[str, Any] = {"role": "system", "content": summary}
        return system_msgs + [summary_msg] + selected

    return system_msgs + selected
