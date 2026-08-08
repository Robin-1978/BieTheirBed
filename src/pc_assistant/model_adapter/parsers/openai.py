from __future__ import annotations

import json
from typing import Any

from pc_assistant.model_adapter.content import split_content, to_openai_content
from pc_assistant.model_adapter.types import StreamChunk


def apply_cache_control(messages: list[dict[str, Any]], cache_control: dict[str, Any] | None) -> None:
    """Attach cache_control to the system message in place, if present."""
    if not cache_control:
        return
    for m in messages:
        if m.get("role") == "system":
            m["cache_control"] = cache_control
            break


def build_chat_payload(
    model_name: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
    temperature: float = 0.7,
    max_tokens: int = 1024,
    tool_choice: str | dict[str, Any] | None = None,
    *,
    cache_prompt: bool = False,
    stream_options: bool = False,
    thinking: dict[str, Any] | None = None,
) -> dict[str, Any]:
    serialized = []
    for m in messages:
        msg = dict(m)
        content = m.get("content")
        if m.get("role") == "tool" and isinstance(content, list):
            blocks = split_content(content)
            text = "".join(
                str(block.get("text", "")) for block in blocks
                if block.get("type") == "text"
            ) or "[visual tool result]"
            tool_msg = dict(msg)
            tool_msg["content"] = text
            serialized.append(tool_msg)
            image_blocks = [block for block in blocks if block.get("type") == "image"]
            if image_blocks:
                serialized.append({
                    "role": "user",
                    "content": to_openai_content([
                        {"type": "text", "text": "Visual observation returned by the preceding tool."},
                        *image_blocks,
                    ]),
                })
        else:
            msg["content"] = to_openai_content(content)
            serialized.append(msg)
    payload: dict[str, Any] = {
        "model": model_name,
        "messages": serialized,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if cache_prompt:
        payload["cache_prompt"] = True
    if stream_options:
        payload["stream_options"] = {"include_usage": True}
    if thinking is not None:
        payload["thinking"] = dict(thinking)
    if tools:
        payload["tools"] = tools
    if tool_choice is not None:
        payload["tool_choice"] = tool_choice
    return payload


class OpenAIStreamAccumulator:
    """Stateful accumulation of OpenAI-style SSE chunks into StreamChunks."""

    def __init__(self) -> None:
        self.accumulated_tool_calls: dict[int, dict[str, Any]] = {}
        self.last_finish_reason = ""
        self.last_usage: dict[str, Any] = {}

    def process_chunk(self, chunk_data: dict[str, Any]) -> list[StreamChunk]:
        chunks: list[StreamChunk] = []

        chunk_usage = chunk_data.get("usage")
        if chunk_usage and isinstance(chunk_usage, dict):
            self.last_usage = chunk_usage

        choices = chunk_data.get("choices", [])
        if not choices:
            return chunks
        delta = choices[0].get("delta", {})
        finish_reason = choices[0].get("finish_reason", "")
        if finish_reason:
            self.last_finish_reason = finish_reason

        delta_content = delta.get("content", "") or ""
        delta_thinking = delta.get("reasoning_content", "") or delta.get("thinking", "") or ""

        delta_tool_calls: list[dict[str, Any]] = []
        for dtc in delta.get("tool_calls", []):
            idx = dtc.get("index", 0)
            if idx not in self.accumulated_tool_calls:
                self.accumulated_tool_calls[idx] = {
                    "id": dtc.get("id", ""),
                    "type": "function",
                    "function": {
                        "name": "",
                        "arguments": "",
                    },
                }
            acc = self.accumulated_tool_calls[idx]
            if dtc.get("id"):
                acc["id"] = dtc["id"]
            func_delta = dtc.get("function", {})
            if func_delta.get("name"):
                acc["function"]["name"] += func_delta["name"]
            if func_delta.get("arguments"):
                acc["function"]["arguments"] += func_delta["arguments"]

        if delta_content or delta_thinking or delta_tool_calls:
            chunks.append(StreamChunk(
                delta_content=delta_content,
                delta_thinking=delta_thinking,
                delta_tool_calls=delta_tool_calls,
                finish_reason="",
            ))
        return chunks

    def finish(self) -> StreamChunk:
        final_tool_calls = list(self.accumulated_tool_calls.values())
        for tc in final_tool_calls:
            if "function" in tc and isinstance(tc["function"].get("arguments"), str):
                try:
                    tc["function"]["arguments"] = json.loads(tc["function"]["arguments"])
                except (json.JSONDecodeError, TypeError):
                    pass
        return StreamChunk(
            delta_content="",
            delta_tool_calls=final_tool_calls,
            finish_reason=self.last_finish_reason,
            usage=self.last_usage,
        )
