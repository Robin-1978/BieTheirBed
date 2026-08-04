from __future__ import annotations

import json
from typing import Any

from pc_assistant.model_adapter.types import LLMResponse, StreamChunk


def convert_tools_to_anthropic(tools: list[dict[str, Any]], cache_control: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    anthropic_tools: list[dict[str, Any]] = []
    for t in tools:
        func = t.get("function", {})
        tool_def = {
            "name": func.get("name", ""),
            "description": func.get("description", ""),
            "input_schema": func.get("parameters", {"type": "object", "properties": {}}),
        }
        if cache_control:
            tool_def["cache_control"] = cache_control
        anthropic_tools.append(tool_def)
    return anthropic_tools


def build_anthropic_payload(
    model_name: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
    temperature: float = 0.7,
    max_tokens: int = 1024,
    cache_control: dict[str, Any] | None = None,
) -> dict[str, Any]:
    system_blocks: list[dict[str, Any]] = []
    filtered_msgs: list[dict[str, Any]] = []
    for m in messages:
        if m["role"] == "system":
            block: dict[str, Any] = {"type": "text", "text": m["content"]}
            if cache_control:
                block["cache_control"] = cache_control
            system_blocks.append(block)
        else:
            filtered_msgs.append(m)

    payload: dict[str, Any] = {
        "model": model_name,
        "messages": filtered_msgs,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    if system_blocks:
        payload["system"] = system_blocks if cache_control else " ".join(b["text"] for b in system_blocks)
    if tools:
        payload["tools"] = convert_tools_to_anthropic(tools, cache_control)
    return payload


def parse_anthropic_response(data: dict[str, Any]) -> LLMResponse:
    content = ""
    tool_calls: list[dict[str, Any]] = []
    for block in data.get("content", []):
        if block.get("type") == "text":
            content += block.get("text", "")
        elif block.get("type") == "tool_use":
            tool_calls.append({
                "id": block.get("id", ""),
                "type": "function",
                "function": {
                    "name": block.get("name", ""),
                    "arguments": block.get("input", {}),
                },
            })
    return LLMResponse(
        content=content,
        tool_calls=tool_calls,
        finish_reason=data.get("stop_reason", ""),
        usage=data.get("usage", {}),
    )


class AnthropicStreamAccumulator:
    """Stateful accumulation of Anthropic SSE events into StreamChunks."""

    def __init__(self) -> None:
        self.content_blocks: dict[int, dict[str, Any]] = {}
        self.last_stop_reason = ""
        self.last_usage: dict[str, Any] = {}

    def process(self, event_type: str, data: dict[str, Any]) -> list[StreamChunk]:
        chunks: list[StreamChunk] = []

        if event_type == "message_start":
            msg = data.get("message", {})
            self.last_usage = msg.get("usage", {})

        elif event_type == "content_block_start":
            idx = data.get("index", 0)
            block = data.get("content_block", {})
            self.content_blocks[idx] = block

        elif event_type == "content_block_delta":
            idx = data.get("index", 0)
            delta = data.get("delta", {})
            delta_type = delta.get("type", "")
            block = self.content_blocks.setdefault(idx, {"type": delta_type})

            if delta_type == "text_delta":
                text = delta.get("text", "")
                block["text"] = block.get("text", "") + text
                chunks.append(StreamChunk(delta_content=text))

            elif delta_type == "input_json_delta":
                partial = delta.get("partial_json", "")
                block["partial_json"] = block.get("partial_json", "") + partial

        elif event_type == "content_block_stop":
            idx = data.get("index", 0)
            block = self.content_blocks.get(idx, {})

            if block.get("type") == "tool_use":
                raw_json = block.get("partial_json", "")
                try:
                    arguments = json.loads(raw_json) if raw_json else {}
                except (json.JSONDecodeError, TypeError):
                    arguments = {}
                tool_call = {
                    "id": block.get("id", ""),
                    "type": "function",
                    "function": {
                        "name": block.get("name", ""),
                        "arguments": arguments,
                    },
                }
                chunks.append(StreamChunk(delta_tool_calls=[tool_call]))

        elif event_type == "message_delta":
            delta = data.get("delta", {})
            self.last_stop_reason = delta.get("stop_reason", "")
            usage = data.get("usage", {})
            if usage:
                self.last_usage = {**self.last_usage, **usage}

        elif event_type == "message_stop":
            chunks.append(StreamChunk(
                finish_reason=self.last_stop_reason,
                usage=self.last_usage,
            ))

        return chunks
