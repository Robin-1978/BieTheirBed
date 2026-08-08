from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class StreamChunk(BaseModel):
    delta_content: str = ""
    delta_thinking: str = ""
    delta_tool_calls: list[dict[str, Any]] = []
    finish_reason: str = ""
    usage: dict[str, Any] = {}
