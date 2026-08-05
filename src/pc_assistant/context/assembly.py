"""LLM message assembly, truncation, and context editing."""
from __future__ import annotations

import json
import logging
from typing import Any

from pc_assistant.context.compact import compact_dialogue_turn
from pc_assistant.context.tags import (
    is_compacted_history,
    is_session_context_message,
    is_strategy_context_message,
)
from pc_assistant.context.prompt import build_session_context
from pc_assistant.model_adapter.content import text_block

logger = logging.getLogger(__name__)

_MAX_TURNS = 8


def _last_n_turns(conversation: list[dict[str, Any]], n: int) -> list[dict[str, Any]]:
    from pc_assistant.context.tags import is_dialogue_user_turn

    if not conversation:
        return []
    boundaries = [i for i, m in enumerate(conversation) if is_dialogue_user_turn(m)]
    if not boundaries:
        return conversation[-30:]
    start_idx = boundaries[-n] if len(boundaries) >= n else 0
    return conversation[start_idx:]


def _split_last_dialogue_turn(
    history: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    from pc_assistant.context.tags import is_dialogue_user_turn

    if not history:
        return [], []
    boundaries = [i for i, m in enumerate(history) if is_dialogue_user_turn(m)]
    if not boundaries:
        return [], history
    last_idx = boundaries[-1]
    if last_idx == 0:
        return [], history
    return history[:last_idx], history[last_idx:]


def assemble_llm_messages(
    system_prompt: str,
    conversation: list[dict[str, Any]],
    last_user_msg: str,
    *,
    working_directory: str = "",
    memory_context: str = "",
    turn_context: str = "",
) -> list[dict[str, Any]]:
    """Build API messages: system → history → session_ctx+memory → current_turn.

    Cache-friendly layout: the system message and the (answered) history are
    byte-identical across turns, so a prompt-cache prefix match is preserved.
    Volatile content (<user_memory>, <session>, current-turn context) is pinned
    to the tail, right before the current user turn.
    """
    history = _last_n_turns(conversation, _MAX_TURNS)
    prefix_hist, current_turn = _split_last_dialogue_turn(history)

    if not current_turn and last_user_msg:
        current_turn = [{"role": "user", "content": last_user_msg}]

    if turn_context and current_turn and current_turn[-1].get("role") == "user":
        last = current_turn[-1]
        if isinstance(last["content"], list):
            last = {**last, "content": [*last["content"], text_block("\n\n" + turn_context)]}
        else:
            last = {**last, "content": last["content"] + "\n\n" + turn_context}
        current_turn[-1] = last

    messages: list[dict[str, Any]] = [{"role": "system", "content": system_prompt}]

    messages.extend(prefix_hist)

    session_ctx = build_session_context(
        working_directory=working_directory,
        memory_context=memory_context,
    )
    if session_ctx:
        messages.append({"role": "user", "content": session_ctx})

    messages.extend(current_turn)
    return _sanitize_tool_calls(messages)


def _sanitize_tool_calls(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Ensure tool_calls[].function.arguments is always a JSON string."""
    for msg in messages:
        tcs = msg.get("tool_calls") or msg.get("delta_tool_calls")
        if not tcs:
            continue
        for tc in tcs:
            func = tc.get("function", {})
            args = func.get("arguments")
            if isinstance(args, str):
                continue
            func["arguments"] = json.dumps(args if args is not None else {}, ensure_ascii=False)
    return messages


def _estimate_tokens(messages: list[dict[str, Any]]) -> int:
    from pc_assistant.context.token_estimate import TokenEstimator
    return TokenEstimator().messages_tokens(messages)


def _context_edit(messages: list[dict[str, Any]], keep_recent_turns: int = 2) -> list[dict[str, Any]]:
    if not messages:
        return messages

    from pc_assistant.context.tags import is_dialogue_user_turn

    turns: list[list[dict[str, Any]]] = []
    current_turn: list[dict[str, Any]] = []
    for msg in messages:
        if is_dialogue_user_turn(msg) and current_turn:
            turns.append(current_turn)
            current_turn = []
        current_turn.append(msg)
    if current_turn:
        turns.append(current_turn)

    if len(turns) <= keep_recent_turns:
        return messages

    old_turns = turns[:-keep_recent_turns]
    recent_turns = turns[-keep_recent_turns:]

    compressed_old: list[dict[str, Any]] = []
    for turn in old_turns:
        compressed_old.extend(
            compact_dialogue_turn(
                turn,
                keep_recent_turns=keep_recent_turns,
                source="context_edit",
            ),
        )

    return compressed_old + [m for t in recent_turns for m in t]


def truncate_messages(
    messages: list[dict[str, Any]],
    budget: int = 4096,
    *,
    keep_recent_turns: int = 2,
) -> list[dict[str, Any]]:
    from pc_assistant.context.filter import trim_stale_content
    from pc_assistant.context.tags import is_dialogue_user_turn

    if not messages:
        return messages

    system = [m for m in messages if m.get("role") == "system"]
    pin_strategy = [m for m in messages if is_strategy_context_message(m)]
    pin_session = [m for m in messages if is_session_context_message(m)]
    others = [
        m for m in messages
        if m.get("role") != "system"
        and not is_strategy_context_message(m)
        and not is_session_context_message(m)
    ]

    summary_block: list[dict[str, Any]] = []
    rest: list[dict[str, Any]] = others
    if others and is_compacted_history(others[0].get("content")):
        summary_block = others[:2]
        rest = others[2:]

    if summary_block:
        rest, _ = trim_stale_content(rest)
    else:
        rest = _context_edit(rest, keep_recent_turns=keep_recent_turns)
        rest, _ = trim_stale_content(rest)

    turns: list[list[dict[str, Any]]] = []
    current_turn: list[dict[str, Any]] = []
    for msg in rest:
        if is_dialogue_user_turn(msg) and current_turn:
            turns.append(current_turn)
            current_turn = []
        current_turn.append(msg)
    if current_turn:
        turns.append(current_turn)

    # Keep the current turn, but drop older turns all the way down to one when
    # the budget is tight. Retaining two oversized turns defeats the budget.
    while turns and len(turns) > 1:
        total = _estimate_tokens(
            system + pin_strategy + pin_session + summary_block + [m for t in turns for m in t],
        )
        if total <= budget:
            break
        turns.pop(0)

    if pin_session and turns:
        body = [m for t in turns[:-1] for m in t]
        tail = [m for t in turns[-1:] for m in t]
        result = system + pin_strategy + summary_block + body + pin_session + tail
    else:
        result = system + pin_strategy + pin_session + summary_block + [m for t in turns for m in t]

    final_tokens = _estimate_tokens(result)
    utilization = final_tokens / budget if budget else 0
    if utilization > 0.7:
        logger.warning(
            "Context utilization %.0f%% (%d/%d tokens, %d turns remaining)",
            utilization * 100, final_tokens, budget, len(turns),
        )
    return result
