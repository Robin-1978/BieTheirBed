"""OpenAI Responses API wire format (``POST {base}/responses``).

Covers the third Zen protocol family alongside Chat Completions
(``openai_compatible``) and Messages (``anthropic``). Only this module
knows the Responses shapes: ``input`` items, ``function`` tools, and the
``response.*`` SSE event stream. The accumulated terminal tool calls reuse
the OpenAI chat shape so ``HttpModelProvider._terminal_chunk`` works
unchanged.
"""
from __future__ import annotations

import json
from typing import Any

from knoa_platform.model_adapter.content import split_content
from knoa_platform.model_adapter.types import StreamChunk


def _text_of(content: Any) -> str:
    """Collapse neutral content to plain text (tool outputs, system)."""
    parts: list[str] = []
    for block in split_content(content):
        if block.get("type") == "text":
            parts.append(str(block.get("text", "")))
    return "".join(parts)


def _user_content(content: Any) -> Any:
    """Map neutral content to Responses ``input_text``/``input_image`` parts."""
    if isinstance(content, str) or content is None:
        return content
    if not isinstance(content, list):
        return str(content)
    out: list[dict[str, Any]] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        kind = block.get("type")
        if kind == "text":
            out.append({"type": "input_text", "text": block.get("text", "")})
        elif kind == "image":
            out.append({
                "type": "input_image",
                "image_url": block.get("image_url", ""),
            })
        else:
            out.append(block)
    return out


def _function_args(arguments: Any) -> str:
    if isinstance(arguments, str):
        return arguments
    try:
        return json.dumps(arguments, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(arguments)


def to_responses_input(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert chat-style history to Responses ``input`` items."""
    items: list[dict[str, Any]] = []
    for message in messages:
        role = str(message.get("role", ""))
        if role == "system":
            text = _text_of(message.get("content"))
            items.append({"role": "system", "content": text})
        elif role == "user":
            items.append({"role": "user", "content": _user_content(message.get("content"))})
        elif role == "assistant":
            content = message.get("content")
            text = _text_of(content) if not isinstance(content, str) else content
            raw_calls = message.get("tool_calls") or []
            if text:
                items.append({"role": "assistant", "content": text})
            for call in raw_calls:
                if not isinstance(call, dict):
                    continue
                function = call.get("function") if isinstance(call.get("function"), dict) else {}
                items.append({
                    "type": "function_call",
                    "call_id": str(call.get("id") or function.get("id") or ""),
                    "name": str(function.get("name") or call.get("name") or ""),
                    "arguments": _function_args(function.get("arguments", {})),
                })
        elif role == "tool":
            items.append({
                "type": "function_call_output",
                "call_id": str(message.get("tool_call_id", "")),
                "output": _text_of(message.get("content")),
            })
        else:
            items.append({"role": "user", "content": _text_of(message.get("content"))})
    return items


def to_responses_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "name": definition.get("name", ""),
            "description": definition.get("description", ""),
            "parameters": definition.get(
                "inputSchema",
                {"type": "object", "properties": {}},
            ),
        }
        for definition in tools
    ]


def build_responses_payload(
    model_name: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
    temperature: float = 0.7,
    max_tokens: int = 1024,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": model_name,
        "input": to_responses_input(messages),
        "temperature": temperature,
        "max_output_tokens": max_tokens,
    }
    if tools:
        payload["tools"] = to_responses_tools(tools)
    return payload


class ResponsesStreamAccumulator:
    """Accumulate Responses SSE events (``response.*``) into StreamChunks."""

    def __init__(self) -> None:
        self.calls: dict[int, dict[str, Any]] = {}
        self.usage: dict[str, Any] = {}
        self.failed = False
        self.incomplete_reason = ""

    def _call(self, index: int) -> dict[str, Any]:
        return self.calls.setdefault(
            index, {"id": "", "name": "", "arguments": ""}
        )

    def process_event(self, event_type: str, data: dict[str, Any]) -> list[StreamChunk]:
        chunks: list[StreamChunk] = []
        if event_type == "response.output_text.delta":
            delta = str(data.get("delta", ""))
            if delta:
                chunks.append(StreamChunk(delta_content=delta))
        elif event_type == "response.reasoning_summary_text.delta":
            delta = str(data.get("delta", ""))
            if delta:
                chunks.append(StreamChunk(delta_thinking=delta))
        elif event_type == "response.output_item.added":
            item = data.get("item") if isinstance(data.get("item"), dict) else {}
            if item.get("type") == "function_call":
                call = self._call(int(data.get("output_index", 0)))
                call["id"] = str(item.get("call_id", "") or call["id"])
                call["name"] = str(item.get("name", "") or call["name"])
        elif event_type == "response.function_call_arguments.delta":
            call = self._call(int(data.get("output_index", 0)))
            call["arguments"] += str(data.get("delta", ""))
        elif event_type == "response.function_call_arguments.done":
            call = self._call(int(data.get("output_index", 0)))
            if isinstance(data.get("arguments"), str):
                call["arguments"] = data["arguments"]
        elif event_type == "response.completed":
            response = data.get("response") if isinstance(data.get("response"), dict) else {}
            usage = response.get("usage") if isinstance(response.get("usage"), dict) else {}
            if usage:
                self.usage = {
                    "prompt_tokens": usage.get("input_tokens", 0),
                    "completion_tokens": usage.get("output_tokens", 0),
                    "total_tokens": usage.get("total_tokens", 0),
                }
        elif event_type == "response.incomplete":
            response = data.get("response") if isinstance(data.get("response"), dict) else {}
            details = response.get("incomplete_details") if isinstance(
                response.get("incomplete_details"), dict
            ) else {}
            self.incomplete_reason = str(details.get("reason", ""))
        elif event_type in {"response.failed", "error"}:
            self.failed = True
        return chunks

    def finish(self) -> StreamChunk:
        tool_calls = [
            {
                "id": call["id"],
                "type": "function",
                "function": {"name": call["name"], "arguments": call["arguments"]},
            }
            for _, call in sorted(self.calls.items())
            if call["name"] or call["arguments"]
        ]
        for call in tool_calls:
            arguments = call["function"]["arguments"]
            if isinstance(arguments, str) and arguments:
                try:
                    call["function"]["arguments"] = json.loads(arguments)
                except (json.JSONDecodeError, TypeError):
                    pass
        if self.failed:
            finish_reason = "error"
        elif tool_calls:
            finish_reason = "tool_calls"
        elif self.incomplete_reason == "max_output_tokens":
            finish_reason = "length"
        else:
            finish_reason = "stop"
        return StreamChunk(
            delta_content="",
            delta_tool_calls=tool_calls,
            finish_reason=finish_reason,
            usage=self.usage,
        )
