from __future__ import annotations

from datetime import datetime
from typing import Any, Callable

from pydantic import BaseModel, Field


class Message(BaseModel):
    role: str
    content: str
    tool_calls: list[dict[str, Any]] | None = None
    tool_call_id: str | None = None


def _build_date_context() -> str:
    now = datetime.now()
    weekday_names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    return f"Current date: {now.strftime('%Y-%m-%d')} ({weekday_names[now.weekday()]})\nCurrent time: {now.strftime('%H:%M:%S')}"


class ConversationManager:
    def __init__(self, max_messages: int = 100) -> None:
        self._messages: list[Message] = []
        self._max_messages = max_messages
        self._system_prompt: str = ""
        self._date_context_provider: Callable[[], str] = _build_date_context

    def set_system_context(self, system_prompt: str, date_context_provider: Callable[[], str] | None = None) -> None:
        self._system_prompt = system_prompt
        if date_context_provider is not None:
            self._date_context_provider = date_context_provider

    def add(self, role: str, content: str, **kwargs: Any) -> Message:
        if role == "system":
            raise ValueError("System messages must be set via set_system_context(), not add()")
        msg = Message(role=role, content=content, **kwargs)
        self._messages.append(msg)
        return msg

    def add_user(self, content: str) -> Message:
        return self.add("user", content)

    def add_assistant(self, content: str, tool_calls: list[dict[str, Any]] | None = None) -> Message:
        return self.add("assistant", content, tool_calls=tool_calls)

    def add_assistant_final(self, content: str) -> Message:
        """Store final assistant response without tool_calls (to prevent AI confusion in history)."""
        return self.add("assistant", content, tool_calls=None)

    def add_tool_result(self, tool_call_id: str, content: str) -> Message:
        return self.add("tool", content, tool_call_id=tool_call_id)

    def get_messages(self) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for msg in self._messages:
            d: dict[str, Any] = {"role": msg.role, "content": msg.content}
            if msg.tool_calls is not None:
                d["tool_calls"] = msg.tool_calls
            if msg.tool_call_id is not None:
                d["tool_call_id"] = msg.tool_call_id
            result.append(d)
        return result

    def get_messages_for_llm(self) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []

        system_parts = []
        if self._system_prompt:
            system_parts.append(self._system_prompt)
        date_ctx = self._date_context_provider()
        if date_ctx:
            system_parts.append(date_ctx)
        if system_parts:
            result.append({"role": "system", "content": "\n\n".join(system_parts)})

        skip_tool = False
        for msg in self._messages:
            if msg.role == "system":
                continue
            elif msg.role == "user":
                skip_tool = True
                result.append({"role": "user", "content": msg.content})
            elif msg.role == "assistant":
                d: dict[str, Any] = {"role": "assistant", "content": msg.content}
                if msg.tool_calls:
                    d["tool_calls"] = msg.tool_calls
                    skip_tool = False
                else:
                    skip_tool = True
                result.append(d)
            elif msg.role == "tool":
                if not skip_tool:
                    result.append({
                        "role": "tool",
                        "content": msg.content,
                        "tool_call_id": msg.tool_call_id or "",
                    })
            else:
                skip_tool = True
                result.append({"role": msg.role, "content": msg.content})

        return result

    def estimate_token_count(self) -> int:
        from pc_assistant.context.truncator import _estimate_tokens
        total = 0
        for msg in self._messages:
            total += _estimate_tokens(msg.content)
            if msg.tool_calls is not None:
                for tc in msg.tool_calls:
                    total += _estimate_tokens(str(tc))
        return total

    def clear(self) -> None:
        self._messages.clear()

    def __len__(self) -> int:
        return len(self._messages)
