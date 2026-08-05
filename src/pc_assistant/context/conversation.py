from __future__ import annotations

from datetime import datetime
from typing import Any, Callable

from pydantic import BaseModel

from pc_assistant.context.tags import (
    parse_tool_result_payload,
    tool_result_status,
    unwrap_tool_result,
    wrap_tool_result,
)


def _assert_reference_only(content: Any) -> None:
    """Fail closed if provider image payloads reach durable conversation state."""
    if isinstance(content, str):
        if "data:image/" in content and ";base64," in content:
            raise ValueError("Binary image data cannot be stored in conversation history")
        return
    if isinstance(content, list):
        for block in content:
            _assert_reference_only(block)
        return
    if isinstance(content, dict):
        if content.get("type") in ("image", "image_url") or "image_url" in content:
            raise ValueError("Provider image blocks cannot be stored in conversation history")
        for value in content.values():
            _assert_reference_only(value)


class Message(BaseModel):
    role: str
    content: str | list[dict[str, Any]] = ""
    tool_calls: list[dict[str, Any]] | None = None
    delta_tool_calls: list[dict[str, Any]] | None = None
    tool_call_id: str | None = None
    reasoning_content: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"role": self.role, "content": self.content}
        if self.tool_calls is not None:
            d["tool_calls"] = self.tool_calls
        if self.delta_tool_calls is not None:
            d["delta_tool_calls"] = self.delta_tool_calls
        if self.tool_call_id is not None:
            d["tool_call_id"] = self.tool_call_id
        if self.reasoning_content is not None:
            d["reasoning_content"] = self.reasoning_content
        return d


def _build_date_context() -> str:
    now = datetime.now()
    weekday_names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    return f"Current date: {now.strftime('%Y-%m-%d')} ({weekday_names[now.weekday()]})\nCurrent time: {now.strftime('%H:%M:%S')}"


class ConversationManager:
    def __init__(self, max_messages: int = 100) -> None:
        self._messages: list[Message] = []
        self._max_messages = max(2, int(max_messages))
        self._system_prompt: str = ""
        self._date_context_provider: Callable[[], str] = _build_date_context
        # Lossy prompt-only view.  Canonical transcript in ``_messages`` is
        # never replaced by automatic summarization.
        self._context_summary: str = ""
        self._context_summary_turns: int = 0

    def set_system_context(self, system_prompt: str, date_context_provider: Callable[[], str] | None = None) -> None:
        self._system_prompt = system_prompt
        if date_context_provider is not None:
            self._date_context_provider = date_context_provider

    def add(self, role: str, content: str | list[dict[str, Any]], **kwargs: Any) -> Message:
        if role == "system":
            raise ValueError("System messages must be set via set_system_context(), not add()")
        _assert_reference_only(content)
        # Normalize tool_calls/delta_tool_calls
        if "delta_tool_calls" in kwargs and "tool_calls" not in kwargs:
            kwargs["tool_calls"] = kwargs["delta_tool_calls"]
        msg = Message(role=role, content=content, **kwargs)
        self._messages.append(msg)
        self._enforce_limit()
        return msg

    def _enforce_limit(self) -> None:
        while len(self._messages) > self._max_messages:
            # Remove a complete oldest dialogue turn. This avoids leaving a
            # tool result without its assistant tool call in retained state.
            first_user = next(
                (i for i, item in enumerate(self._messages) if item.role == "user"),
                None,
            )
            if first_user is None:
                del self._messages[0]
                continue
            next_user = next(
                (i for i in range(first_user + 1, len(self._messages))
                 if self._messages[i].role == "user"),
                len(self._messages),
            )
            del self._messages[first_user:next_user]

    def add_user(self, content: str | list[dict[str, Any]]) -> Message:
        return self.add("user", content)

    def add_user_with_blocks(
        self,
        text: str,
        blocks: list[dict[str, Any]] | None = None,
    ) -> Message:
        """Add a user turn that carries multimodal blocks (text + images)."""
        if blocks:
            content: list[dict[str, Any]] = list(blocks)
            if text:
                content.insert(0, {"type": "text", "text": text})
            return self.add("user", content)
        return self.add("user", text)

    def add_assistant(self, content: str, tool_calls: list[dict[str, Any]] | None = None, delta_tool_calls: list[dict[str, Any]] | None = None, reasoning_content: str | None = None) -> Message:
        tcs = tool_calls or delta_tool_calls
        return self.add("assistant", content, tool_calls=tcs, delta_tool_calls=tcs, reasoning_content=reasoning_content)

    def add_assistant_final(self, content: str) -> Message:
        """Store final assistant response without tool_calls."""
        return self.add("assistant", content, tool_calls=None, delta_tool_calls=None)

    def add_tool_result(self, tool_call_id: str, content: str, tool_name: str = "") -> Message:
        """Store tool result, optionally wrapped in XML tags for structured context."""
        if tool_name:
            wrapped = wrap_tool_result(tool_name, content)
            return self.add("tool", wrapped, tool_call_id=tool_call_id)
        return self.add("tool", content, tool_call_id=tool_call_id)

    def add_tool_result_blocks(
        self,
        tool_call_id: str,
        blocks: list[dict[str, Any]],
        tool_name: str = "",
    ) -> Message:
        """Store a tool result as raw content blocks (e.g. an inline image).

        No XML wrapping — vision blocks must reach the provider untouched.
        """
        return self.add("tool", blocks, tool_call_id=tool_call_id)

    def get_messages(self) -> list[dict[str, Any]]:
        return [m.to_dict() for m in self._messages]

    def get_messages_for_llm(self) -> list[dict[str, Any]]:
        """Build messages for LLM API call (system preamble + history)."""
        result: list[dict[str, Any]] = []

        # Build system message
        system_parts = []
        if self._system_prompt:
            system_parts.append(self._system_prompt)
        date_ctx = self._date_context_provider()
        if date_ctx:
            system_parts.append(date_ctx)
        system_prompt = "\n\n".join(system_parts) if system_parts else ""

        if system_prompt:
            result.append({"role": "system", "content": system_prompt})

        result.extend(self._build_history_messages())
        return result

    def get_messages_for_llm_raw(self) -> list[dict[str, Any]]:
        """Raw history without the system preamble (for the assembly pipeline)."""
        history = self._build_history_messages()
        if not self._context_summary:
            return history
        from pc_assistant.context.tags import format_context_summary, is_dialogue_user_turn
        # Keep the latest three dialogue turns verbatim in the prompt view.
        boundaries = [i for i, m in enumerate(history) if is_dialogue_user_turn(m)]
        start = boundaries[-3] if len(boundaries) >= 3 else (boundaries[0] if boundaries else 0)
        return [
            {"role": "user", "content": format_context_summary(
                self._context_summary, covered_turns=self._context_summary_turns,
            )},
            *history[start:],
        ]

    def get_canonical_messages_for_llm_raw(self) -> list[dict[str, Any]]:
        """Full durable transcript, bypassing any lossy prompt-only summary."""
        return self._build_history_messages()

    def set_context_summary(self, summary: str, *, covered_turns: int = 0) -> None:
        self._context_summary = (summary or "").strip()
        self._context_summary_turns = max(0, int(covered_turns))

    def clear_context_summary(self) -> None:
        self._context_summary = ""
        self._context_summary_turns = 0

    @property
    def has_context_summary(self) -> bool:
        return bool(self._context_summary)

    @property
    def context_summary_turns(self) -> int:
        return self._context_summary_turns

    @property
    def context_summary(self) -> str:
        return self._context_summary

    def _build_history_messages(self) -> list[dict[str, Any]]:
        """Build conversation messages shared by the LLM/raw message builders."""
        result: list[dict[str, Any]] = []

        # Build conversation messages
        valid_tool_ids: set[str] = set()
        for msg in self._messages:
            tcs = msg.tool_calls or msg.delta_tool_calls
            if msg.role == "assistant" and tcs:
                for tc in tcs:
                    tc_id = tc.get("id", "")
                    if tc_id:
                        valid_tool_ids.add(tc_id)

        for msg in self._messages:
            if msg.role == "system":
                continue
            elif msg.role == "user":
                result.append({"role": "user", "content": msg.content})
            elif msg.role == "assistant":
                d: dict[str, Any] = {"role": "assistant", "content": msg.content}
                if msg.tool_calls:
                    d["tool_calls"] = msg.tool_calls
                if msg.reasoning_content:
                    d["reasoning_content"] = msg.reasoning_content
                result.append(d)
            elif msg.role == "tool":
                tc_id = msg.tool_call_id or ""
                if tc_id in valid_tool_ids:
                    result.append({
                        "role": "tool",
                        "content": msg.content,
                        "tool_call_id": tc_id,
                    })
            else:
                result.append({"role": msg.role, "content": msg.content})

        return result

    def compress(self, *, keep_recent: int = 4) -> None:
        """Compress conversation history in-place, keeping recent messages full-fidelity."""
        from pc_assistant.context.compact import compress_message_list, strip_ephemeral
        from pc_assistant.context.tags import is_compacted_history, is_context_summary

        raw = self.get_messages_for_llm_raw()
        if any(
            is_compacted_history(message.get("content"))
            or is_context_summary(message.get("content"))
            for message in raw
        ):
            return
        if len(raw) <= keep_recent:
            return

        compressed = compress_message_list(raw, keep_recent=keep_recent, source="user_trim")
        compressed = strip_ephemeral(compressed)
        self.rebuild_from_messages(compressed)

    def compact_completed_tool_results(
        self,
        *,
        max_chars: int = 2000,
        keep_recent_turns: int = 1,
    ) -> int:
        """Replace completed-turn tool payloads with protocol-safe markers.

        The active turn still receives the complete bounded result. Before the
        next user turn, tool-call messages remain for traceability and provider
        pairing, while their bulky result bodies are omitted because the
        assistant's subsequent interpretation is the durable context. Error
        status and artifact IDs are retained when cheaply extractable.
        """
        # Keep the most recent completed dialogue turn verbatim.  A follow-up
        # commonly refers to the immediately preceding tool result.
        dialogue_boundaries = [
            i for i, message in enumerate(self._messages)
            if message.role == "user"
            and not str(message.content).lstrip().startswith("<runtime_context")
            and not str(message.content).lstrip().startswith("<compacted_history")
        ]
        if keep_recent_turns <= 0:
            cutoff = len(self._messages)
        elif len(dialogue_boundaries) > keep_recent_turns:
            cutoff = dialogue_boundaries[-keep_recent_turns]
        else:
            cutoff = 0

        changed = 0
        for index, message in enumerate(self._messages):
            if message.role != "tool" or not isinstance(message.content, str):
                continue
            if index >= cutoff:
                continue
            if message.content.startswith("[tool_result_omitted:"):
                continue
            raw_payload = unwrap_tool_result(message.content)
            # Preserve short results.  Compaction is for oversized payloads,
            # not a blanket replacement of every historical tool response.
            if len(raw_payload) <= max_chars:
                continue

            parsed = parse_tool_result_payload(message.content)
            artifact_ids: list[str] = []
            if isinstance(parsed, dict):
                artifact_id = parsed.get("artifact_id")
                if artifact_id:
                    artifact_ids.append(str(artifact_id))
            status = tool_result_status(message.content)
            artifact_note = f"; artifact_ids={','.join(dict.fromkeys(artifact_ids))}" if artifact_ids else ""
            message.content = (
                f"[tool_result_omitted: prior tool result {status}; "
                f"assistant interpretation is retained{artifact_note}]"
            )
            changed += 1
        return changed

    def rebuild_from_messages(self, messages: list[dict[str, Any]]) -> None:
        """Replace internal history with the given (already assembled) message list."""
        new_messages: list[Message] = []
        for m in messages:
            role = m.get("role", "")
            content = m.get("content", "")
            _assert_reference_only(content)
            if role == "system":
                continue
            tc_id = m.get("tool_call_id")
            msg = Message(
                role=role,
                content=content,
                tool_calls=m.get("tool_calls"),
                tool_call_id=tc_id,
                reasoning_content=m.get("reasoning_content"),
            )
            if m.get("tool_calls"):
                msg.delta_tool_calls = m["tool_calls"]
            new_messages.append(msg)

        self._messages = new_messages

    def estimate_token_count(self) -> int:
        from pc_assistant.context.assembly import _estimate_tokens
        return _estimate_tokens(self.get_messages_for_llm_raw())

    def snapshot_len(self) -> int:
        """Current message count — use as a rollback watermark."""
        return len(self._messages)

    def truncate_to(self, length: int) -> None:
        """Drop messages beyond `length` (rollback after cancel/error)."""
        if 0 <= length < len(self._messages):
            del self._messages[length:]

    def clear(self) -> None:
        self._messages.clear()

    def __len__(self) -> int:
        return len(self._messages)
