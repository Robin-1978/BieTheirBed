from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from pc_assistant.model_adapter.content import ContentBlock  # noqa: F401  (re-export: str | list[dict])


class LLMMessage(BaseModel):
    role: str
    content: str | list[dict[str, Any]]


class ImageAttachment(BaseModel):
    """Image ingress: local path/data URL, or an existing store reference."""

    attachment_id: str | None = None
    path: str | None = None
    data_url: str | None = None
    media_type: str = "image/jpeg"
    caption: str = ""

    @classmethod
    def from_path(cls, path: str | Path, caption: str = "") -> "ImageAttachment":
        return cls(path=str(path), caption=caption)

    @classmethod
    def from_ref(cls, attachment_id: str, caption: str = "") -> "ImageAttachment":
        return cls(attachment_id=attachment_id, caption=caption)


class LLMResponse(BaseModel):
    content: str
    tool_calls: list[dict[str, Any]] = []
    finish_reason: str = ""
    usage: dict[str, Any] = {}


class StreamChunk(BaseModel):
    delta_content: str = ""
    delta_thinking: str = ""
    delta_tool_calls: list[dict[str, Any]] = []
    finish_reason: str = ""
    usage: dict[str, Any] = {}


def normalize_tool_calls(raw_tool_calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Parse JSON-string arguments of OpenAI-style tool calls into dicts."""
    normalized: list[dict[str, Any]] = []
    for tc in raw_tool_calls:
        tc_copy = dict(tc)
        func = tc_copy.get("function", {})
        if isinstance(func, dict):
            args = func.get("arguments")
            if isinstance(args, str):
                try:
                    func["arguments"] = json.loads(args)
                except (json.JSONDecodeError, TypeError):
                    func["arguments"] = {}
            elif args is None:
                func["arguments"] = {}
        tc_copy["function"] = func
        normalized.append(tc_copy)
    return normalized


def format_tool_result_message(tool_call_id: str, content: str) -> dict[str, Any]:
    return {
        "role": "tool",
        "tool_call_id": tool_call_id,
        "content": content,
    }
