from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class MessageType(Enum):
    USER = "user"
    ASSISTANT = "assistant"
    THINK = "think"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    ERROR = "error"
    SYSTEM = "system"


@dataclass
class Message:
    type: MessageType
    content: str
    timestamp: float = field(default_factory=time.time)
    tool_name: str | None = None
    tool_args: dict[str, Any] | None = None
    tool_result: Any = None

    @property
    def id(self) -> str:
        return f"{self.timestamp}-{hash(self.content[:50])}"


@dataclass
class UIState:
    messages: deque[Message] = field(default_factory=lambda: deque(maxlen=2000))
    processing: bool = False
    cancelled: bool = False
    debug_mode: bool = False
    last_input: str = ""

    def add_message(self, msg_type: MessageType, content: str = "", **kwargs) -> Message:
        msg = Message(type=msg_type, content=content, **kwargs)
        self.messages.append(msg)
        return msg

    def clear_messages(self) -> None:
        self.messages.clear()


@dataclass
class AppStatus:
    provider: str = "unknown"
    model: str = "default"
    connected: bool = False
    total_tokens: int = 0
    total_prompt_tokens: int = 0
    total_completion_tokens: int = 0
    total_iterations: int = 0
    memory_items: int = 0

    @property
    def token_str(self) -> str:
        if self.total_tokens >= 1_000_000:
            return f"{self.total_tokens / 1_000_000:.1f}M"
        elif self.total_tokens >= 1000:
            return f"{self.total_tokens / 1000:.1f}k"
        return str(self.total_tokens)
