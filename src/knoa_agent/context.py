"""Knoa Agent-owned model context assembly and compaction."""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from typing import Any
from xml.sax.saxutils import escape

from knoa_agent_contracts import RuntimeTurnContext

_CJK_RE = re.compile(
    r"[\u4e00-\u9fff\u3400-\u4dbf\U00020000-\U0002a6df"
    r"\u3040-\u309f\u30a0-\u30ff\uac00-\ud7af]"
)
_SUMMARY_ACK = "[Context acknowledged - summary is lossy, not verbatim history]"


@dataclass(frozen=True)
class PreparedContext:
    messages: tuple[dict[str, Any], ...]
    model_history: tuple[dict[str, Any], ...]
    durable_history: tuple[dict[str, Any], ...]
    summary: str
    covered_messages: int
    tokens_before: int
    tokens_after: int
    schema_tokens: int
    compacted: bool


class ContextBudgetExceeded(RuntimeError):
    """The current Turn cannot fit even after safe context compaction."""


class TokenEstimator:
    """Small Agent-local estimator; avoids coupling model policy to Platform."""

    def text_tokens(self, value: str) -> int:
        if not value:
            return 0
        cjk = len(_CJK_RE.findall(value))
        return max(0, round(cjk * 1.2 + (len(value) - cjk) / 4.0))

    def messages_tokens(self, messages: list[dict[str, Any]]) -> int:
        total = 0
        for message in messages:
            content = message.get("content") or ""
            if isinstance(content, list):
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    if block.get("type") == "text":
                        total += self.text_tokens(str(block.get("text") or ""))
                    elif block.get("type") in {"image", "image_ref"}:
                        total += 170
            else:
                total += self.text_tokens(str(content))
            calls = message.get("tool_calls")
            if calls:
                total += self.text_tokens(
                    json.dumps(calls, ensure_ascii=False, sort_keys=True, default=str)
                )
        return total


class ContextEngine:
    """Own final prompt layout, token budget, Turn integrity, and summaries."""

    def __init__(
        self,
        *,
        context_window: int,
        completion_reserve: int,
        max_summary_chars: int = 64_000,
        estimator: TokenEstimator | None = None,
    ) -> None:
        if context_window < 512:
            raise ValueError("context_window must be at least 512")
        if completion_reserve < 1 or completion_reserve >= context_window:
            raise ValueError("completion_reserve must fit inside context_window")
        self._context_window = context_window
        self._completion_reserve = completion_reserve
        self._max_summary_chars = max_summary_chars
        self._tokens = estimator or TokenEstimator()

    def prepare(
        self,
        *,
        system_prompt: str,
        model_history: list[dict[str, Any]],
        durable_history: list[dict[str, Any]],
        tools: tuple[dict[str, Any], ...],
        context: RuntimeTurnContext,
        summary: str = "",
        covered_messages: int = 0,
    ) -> PreparedContext:
        if len(model_history) != len(durable_history):
            raise ValueError("Model and durable histories must stay aligned")
        schema_tokens = self._tokens.text_tokens(
            json.dumps(tools, ensure_ascii=False, sort_keys=True, default=str)
        )
        message_budget = self._context_window - self._completion_reserve - schema_tokens
        if message_budget < 256:
            raise ContextBudgetExceeded("Tool schemas consume the model context budget")

        runtime_context = self.render_runtime_context(context)
        before_messages = self._assemble(
            system_prompt,
            model_history,
            summary,
            covered_messages,
            runtime_context,
        )
        tokens_before = self._tokens.messages_tokens(before_messages) + schema_tokens
        if self._tokens.messages_tokens(before_messages) <= message_budget:
            return PreparedContext(
                messages=tuple(before_messages),
                model_history=tuple(model_history),
                durable_history=tuple(durable_history),
                summary=summary,
                covered_messages=covered_messages,
                tokens_before=tokens_before,
                tokens_after=tokens_before,
                schema_tokens=schema_tokens,
                compacted=False,
            )

        model_turns = self._turns(model_history)
        durable_turns = self._turns(durable_history)
        changed = False
        while len(model_turns) > 1:
            candidate_model = [message for turn in model_turns for message in turn]
            candidate = self._assemble(
                system_prompt,
                candidate_model,
                summary,
                covered_messages,
                runtime_context,
            )
            if self._tokens.messages_tokens(candidate) <= message_budget:
                break
            model_turns.pop(0)
            removed_durable = durable_turns.pop(0)
            summary = self._merge_summary(summary, self._summarize_turn(removed_durable))
            covered_messages += len(removed_durable)
            changed = True

        compact_model = [message for turn in model_turns for message in turn]
        compact_durable = [message for turn in durable_turns for message in turn]
        assembled = self._assemble(
            system_prompt,
            compact_model,
            summary,
            covered_messages,
            runtime_context,
        )
        if self._tokens.messages_tokens(assembled) > message_budget:
            trimmed_model, trimmed_durable = self._trim_stale_tool_content(
                compact_model, compact_durable
            )
            changed = changed or trimmed_model != compact_model
            compact_model, compact_durable = trimmed_model, trimmed_durable
            assembled = self._assemble(
                system_prompt,
                compact_model,
                summary,
                covered_messages,
                runtime_context,
            )
        if self._tokens.messages_tokens(assembled) > message_budget and summary:
            summary = self._fit_summary(
                summary,
                system_prompt=system_prompt,
                history=compact_model,
                covered_messages=covered_messages,
                runtime_context=runtime_context,
                message_budget=message_budget,
            )
            changed = True
            assembled = self._assemble(
                system_prompt,
                compact_model,
                summary,
                covered_messages,
                runtime_context,
            )
        message_tokens = self._tokens.messages_tokens(assembled)
        if message_tokens > message_budget:
            # Attempt emergency in-turn progressive compaction before failing
            emerg_model, emerg_durable = self._emergency_in_turn_compaction(
                compact_model, compact_durable
            )
            if emerg_model != compact_model:
                compact_model, compact_durable = emerg_model, emerg_durable
                changed = True
                assembled = self._assemble(
                    system_prompt,
                    compact_model,
                    summary,
                    covered_messages,
                    runtime_context,
                )
                message_tokens = self._tokens.messages_tokens(assembled)

        if message_tokens > message_budget:
            raise ContextBudgetExceeded("Current Turn exceeds the model context budget")
        return PreparedContext(
            messages=tuple(assembled),
            model_history=tuple(compact_model),
            durable_history=tuple(compact_durable),
            summary=summary,
            covered_messages=covered_messages,
            tokens_before=tokens_before,
            tokens_after=message_tokens + schema_tokens,
            schema_tokens=schema_tokens,
            compacted=changed,
        )

    @staticmethod
    def render_runtime_context(context: RuntimeTurnContext) -> str:
        parts = ["<runtime_context>", "<session>"]
        if context.core_memory or context.relevant_memory:
            parts.append("<user_memory>")
            if context.core_memory:
                parts.append("<core>")
                parts.extend(f"- {escape(item)}" for item in context.core_memory)
                parts.append("</core>")
            if context.relevant_memory:
                parts.append("<relevant>")
                parts.extend(f"- {escape(item)}" for item in context.relevant_memory)
                parts.append("</relevant>")
            parts.append("</user_memory>")
        if context.episodic_memory:
            parts.append("<episodic_memory>")
            parts.extend(f"- {escape(item)}" for item in context.episodic_memory)
            parts.append("</episodic_memory>")
        if context.skill_instructions:
            parts.append(context.skill_instructions)
        parts.append(f"<current_time>{time.strftime('%Y-%m-%d %H:%M %A')}</current_time>")
        parts.extend(["</session>", "</runtime_context>"])
        return "\n".join(parts)

    def _assemble(
        self,
        system_prompt: str,
        history: list[dict[str, Any]],
        summary: str,
        covered_messages: int,
        runtime_context: str,
    ) -> list[dict[str, Any]]:
        current_index = self._current_turn_index(history)
        prefix = history[:current_index]
        current = history[current_index:]
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt}
        ]
        messages.extend(self._summary_messages(summary, covered_messages))
        messages.extend(prefix)
        if runtime_context:
            messages.append({"role": "user", "content": runtime_context})
        messages.extend(current)
        return messages

    @staticmethod
    def _current_turn_index(history: list[dict[str, Any]]) -> int:
        indexes = [
            index for index, message in enumerate(history)
            if message.get("role") == "user"
        ]
        return indexes[-1] if indexes else len(history)

    @staticmethod
    def _turns(history: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
        turns: list[list[dict[str, Any]]] = []
        current: list[dict[str, Any]] = []
        for message in history:
            if message.get("role") == "user" and current:
                turns.append(current)
                current = []
            current.append(message)
        if current:
            turns.append(current)
        return turns

    @staticmethod
    def _summary_messages(summary: str, covered_messages: int) -> list[dict[str, Any]]:
        if not summary.strip():
            return []
        content = (
            '<compacted_history lossy="true" auto_compacted="true" '
            f'covered_messages="{covered_messages}" source="knoa_agent">\n'
            f"{summary}\n</compacted_history>"
        )
        return [
            {"role": "user", "content": content},
            {"role": "assistant", "content": _SUMMARY_ACK},
        ]

    def _summarize_turn(self, turn: list[dict[str, Any]]) -> str:
        lines: list[str] = []
        user = [message for message in turn if message.get("role") == "user"]
        if user:
            lines.append("User: " + self._bounded(self._content(user[-1]), 1200))
        calls: dict[str, str] = {}
        for message in turn:
            for call in message.get("tool_calls") or ():
                if not isinstance(call, dict):
                    continue
                function = call.get("function") or {}
                call_id = str(call.get("id") or "")
                name = str(function.get("name") or "unknown")
                arguments = str(function.get("arguments") or "")
                calls[call_id] = name
                lines.append(f"Tool: {name} {self._bounded(arguments, 300)}")
            if message.get("role") == "tool":
                name = calls.get(str(message.get("tool_call_id") or ""), "tool")
                lines.append(
                    f"Result {name}: {self._bounded(self._content(message), 600)}"
                )
        assistants = [
            message for message in turn
            if message.get("role") == "assistant" and not message.get("tool_calls")
        ]
        if assistants:
            lines.append(
                "Assistant: " + self._bounded(self._content(assistants[-1]), 1800)
            )
        return "\n".join(line for line in lines if line.strip())

    def _merge_summary(self, existing: str, addition: str) -> str:
        merged = "\n".join(item for item in (existing.strip(), addition.strip()) if item)
        if len(merged) <= self._max_summary_chars:
            return merged
        return "[Earlier compacted context omitted]\n" + merged[-self._max_summary_chars :]

    def _fit_summary(
        self,
        summary: str,
        *,
        system_prompt: str,
        history: list[dict[str, Any]],
        covered_messages: int,
        runtime_context: str,
        message_budget: int,
    ) -> str:
        lines = summary.splitlines()
        while lines:
            candidate = "\n".join(lines)
            messages = self._assemble(
                system_prompt,
                history,
                candidate,
                covered_messages,
                runtime_context,
            )
            if self._tokens.messages_tokens(messages) <= message_budget:
                return candidate
            assistant = next(
                (index for index, line in enumerate(lines) if line.startswith("Assistant:")),
                None,
            )
            if assistant is not None:
                lines.pop(assistant)
                continue
            longest = max(range(len(lines)), key=lambda index: len(lines[index]))
            label, separator, value = lines[longest].partition(": ")
            minimum = 80 if label == "User" else 48
            if separator and len(value) > minimum:
                new_limit = max(minimum, int(len(value) * 0.7))
                lines[longest] = f"{label}: {self._bounded(value, new_limit)}"
                continue
            removable = next(
                (index for index, line in enumerate(lines) if not line.startswith("User:")),
                0,
            )
            lines.pop(removable)
        return ""

    @staticmethod
    def _content(message: dict[str, Any]) -> str:
        content = message.get("content") or ""
        if isinstance(content, list):
            return "".join(
                str(block.get("text") or "")
                for block in content
                if isinstance(block, dict) and block.get("type") == "text"
            )
        return str(content)

    @staticmethod
    def _bounded(value: str, limit: int) -> str:
        normalized = " ".join(value.split())
        return normalized if len(normalized) <= limit else normalized[: limit - 1] + "…"

    def _trim_stale_tool_content(
        self,
        model: list[dict[str, Any]],
        durable: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        model_out = [dict(message) for message in model]
        durable_out = [dict(message) for message in durable]
        current_index = self._current_turn_index(model_out)

        tool_indices = [
            index
            for index in range(current_index, len(model_out))
            if model_out[index].get("role") == "tool"
        ]
        if not tool_indices:
            return model_out, durable_out

        # The latest tool observation preserves higher fidelity so the model can inspect it.
        # Older tool observations in earlier iterations of the same turn are aggressively trimmed.
        latest_tool_index = tool_indices[-1]

        for index in tool_indices:
            content = self._content(model_out[index])
            is_latest = index == latest_tool_index
            max_limit = 1200 if is_latest else 350
            trim_to = 800 if is_latest else 250

            if len(content) <= max_limit:
                continue
            preview = (
                self._bounded(content, trim_to)
                + f" [trimmed from {len(content)} chars]"
            )
            model_out[index]["content"] = preview
            durable_out[index]["content"] = preview
        return model_out, durable_out

    def _emergency_in_turn_compaction(
        self,
        model: list[dict[str, Any]],
        durable: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        model_out = [dict(message) for message in model]
        durable_out = [dict(message) for message in durable]
        current_index = self._current_turn_index(model_out)

        # 1. First pass: aggressively compress all tool results in the current turn
        for index in range(current_index, len(model_out)):
            if model_out[index].get("role") == "tool":
                content = self._content(model_out[index])
                if len(content) > 180:
                    preview = self._bounded(content, 150) + " [compacted]"
                    model_out[index]["content"] = preview
                    durable_out[index]["content"] = preview

        # 2. Second pass: trim older assistant thoughts in the current turn
        assistant_indices = [
            index
            for index in range(current_index, len(model_out))
            if model_out[index].get("role") == "assistant"
        ]
        if len(assistant_indices) > 1:
            for index in assistant_indices[:-1]:
                content = self._content(model_out[index])
                if len(content) > 250:
                    preview = self._bounded(content, 200) + " [thought compacted]"
                    model_out[index]["content"] = preview
                    durable_out[index]["content"] = preview

        return model_out, durable_out
