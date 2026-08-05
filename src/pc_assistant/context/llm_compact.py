"""Two-level context compaction.

Level 1 (heuristic, always available) is the linear fact extraction in
`context.compact`. Level 2 asks the LLM to rewrite the extracted facts into a
lossy `<compacted_history>` summary block, yielding a much denser history. If the
LLM call fails, the caller falls back to the heuristic result so compaction never
hard-fails.
"""
from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

from pc_assistant.context.compact import (
    COMPACTED_HISTORY_ACK,
    compress_message_list,
    strip_ephemeral,
)
from pc_assistant.context.tags import format_compacted_history

logger = logging.getLogger(__name__)

# `llm_call` receives a plain message list and returns the assistant text.
LlmCallable = Callable[[list[dict[str, Any]]], Awaitable[str]]

_SUMMARY_SYSTEM = (
    "Summarize earlier conversation for a general PC assistant. Be concise and factual; "
    "do not invent. Format the result as compact Markdown using exactly these headings: "
    "Topic, User goal, Done, Current state, Next step, Blockers, Files/apps. "
    "Use bullets only for Done and Files/apps. Keep under 400 tokens."
)

_SUMMARY_TMPL = (
    "Below are the earlier messages of a conversation (oldest first). "
    "Write a short working-state summary, keeping decisions, important facts, tool outcomes, "
    "and files/apps needed to continue. Do not include chain-of-thought or new information.\n\n"
    "---\n{history}\n---"
)


def build_summary_request(history: str) -> list[dict[str, Any]]:
    return [
        {"role": "system", "content": _SUMMARY_SYSTEM},
        {"role": "user", "content": _SUMMARY_TMPL.format(history=history)},
    ]


async def llm_summarize_facts(
    history: str,
    llm_call: LlmCallable,
) -> str | None:
    """Ask the LLM to summarize raw history text. Returns None on any failure."""
    try:
        result = await llm_call(build_summary_request(history))
        result = (result or "").strip()
        return result or None
    except Exception as e:  # noqa: BLE001 - compaction must never hard-fail
        logger.warning("[LLMCompact] summarization failed, falling back: %s", e)
        return None


async def summarize_prompt_history(
    messages: list[dict[str, Any]],
    *,
    keep_recent_turns: int = 3,
    llm_call: LlmCallable | None = None,
) -> tuple[str, int] | None:
    """Return a prompt-only working-state summary and covered turn count."""
    if llm_call is None:
        return None
    from pc_assistant.context.tags import is_dialogue_user_turn
    boundaries = [i for i, m in enumerate(messages) if is_dialogue_user_turn(m)]
    if len(boundaries) <= keep_recent_turns:
        return None
    cutoff = boundaries[-keep_recent_turns]
    old = messages[:cutoff]
    history = "\n".join(_message_line(m) for m in old if m.get("role") != "system")
    summary = await llm_summarize_facts(history, llm_call)
    if not summary:
        return None
    return summary, len(boundaries) - keep_recent_turns


async def compact_conversation_llm(
    messages: list[dict[str, Any]],
    *,
    keep_recent: int = 4,
    llm_call: LlmCallable | None = None,
    source: str = "llm_compact",
) -> list[dict[str, Any]] | None:
    """Compress `messages`, using the LLM to densify the summary block.

    Returns None when there is nothing to compress or the LLM path is unavailable,
    so callers can fall back to the heuristic `compress_message_list`.
    """
    if len(messages) <= keep_recent:
        return None
    if llm_call is None:
        return None

    heuristic = compress_message_list(messages, keep_recent=keep_recent, source=source)
    heuristic = strip_ephemeral(heuristic)
    if not heuristic:
        return None

    old_count = len(messages) - keep_recent
    recent = messages[-keep_recent:]

    # Raw history text for the LLM (don't leak the recent full-fidelity turns).
    old_messages = [m for m in heuristic if m.get("role") != "system"]
    history_text = "\n".join(_message_line(m) for m in old_messages)
    summary_body = await llm_summarize_facts(history_text, llm_call)
    if summary_body is None:
        return None

    block = format_compacted_history(
        [line for line in summary_body.splitlines() if line.strip()],
        covered_messages=old_count,
        keep_recent=keep_recent,
        source=source,
    )
    return [
        {"role": "user", "content": block},
        {"role": "assistant", "content": COMPACTED_HISTORY_ACK},
        *recent,
    ]


def _message_line(m: dict[str, Any]) -> str:
    role = m.get("role", "unknown")
    content = m.get("content", "")
    if isinstance(content, list):
        content = " ".join(
            b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"
        )
    tcs = m.get("tool_calls") or m.get("delta_tool_calls")
    if tcs:
        names = ", ".join(
            (tc.get("function") or {}).get("name", "?") for tc in tcs
        )
        content = f"{content} [tools: {names}]"
    return f"[{role}] {str(content)[:2000]}"
