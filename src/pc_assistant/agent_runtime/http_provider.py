"""Direct HTTP implementations of the target-state ModelProviderPort."""
from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Callable
from typing import Any

import httpx

from pc_assistant.agent_runtime.contracts import HealthStatus
from pc_assistant.agent_runtime.model_step import (
    ModelProviderPort,
    ProviderCallRequest,
    ProviderChunk,
)
from pc_assistant.agent_runtime.tool_step import ProposedToolCall
from pc_assistant.config import ResolvedModelConfig
from pc_assistant.model_adapter.parsers.anthropic import (
    AnthropicStreamAccumulator,
    build_anthropic_payload,
)
from pc_assistant.model_adapter.parsers.openai import (
    OpenAIStreamAccumulator,
    build_chat_payload,
)
from pc_assistant.model_adapter.profiles import resolve_profile
from pc_assistant.tools.http_limits import iter_limited_lines


_MAX_STREAM_LINE_BYTES = 2 * 1024 * 1024
_MAX_MODEL_STREAM_BYTES = 16 * 1024 * 1024


def _finish_reason(raw: str) -> str:
    normalized = raw.strip().lower()
    mapping = {
        "stop": "stop",
        "end_turn": "stop",
        "stop_sequence": "stop",
        "eos": "stop",
        "tool_calls": "tool_calls",
        "tool_use": "tool_calls",
        "length": "length",
        "max_tokens": "length",
        "error": "error",
        "content_filter": "error",
    }
    return mapping.get(normalized, "error")


def _tool_calls(raw_calls: list[dict[str, Any]]) -> tuple[ProposedToolCall, ...]:
    proposed: list[ProposedToolCall] = []
    for raw in raw_calls:
        function = raw.get("function")
        if not isinstance(function, dict):
            raise ValueError("Provider tool call is missing function data")
        arguments = function.get("arguments", {})
        if isinstance(arguments, str):
            arguments = json.loads(arguments)
        if not isinstance(arguments, dict):
            raise ValueError("Provider tool arguments must be an object")
        proposed.append(
            ProposedToolCall(
                call_id=str(raw.get("id") or ""),
                name=str(function.get("name") or ""),
                arguments=arguments,
            )
        )
    return tuple(proposed)


class HttpModelProvider(ModelProviderPort):
    """Stream one resolved OpenAI-compatible or Anthropic model directly."""

    def __init__(
        self,
        model: ResolvedModelConfig,
        *,
        client_factory: Callable[..., Any] = httpx.AsyncClient,
    ) -> None:
        self._model = model
        self._profile = resolve_profile(
            model.driver,
            model.server_url,
            model.api_key,
            model.api_base,
            supports_vision=model.supports_vision,
        )
        self._client_factory = client_factory

    @property
    def model_alias(self) -> str:
        return self._model.alias

    def stream(
        self,
        request: ProviderCallRequest,
        cancellation: asyncio.Event,
    ) -> AsyncIterator[ProviderChunk]:
        if self._profile.anthropic_style:
            return self._stream_anthropic(request, cancellation)
        return self._stream_openai(request, cancellation)

    async def health_check(self) -> HealthStatus:
        try:
            async with self._client_factory(timeout=5.0) as client:
                async with client.stream(
                    "GET",
                    self._profile.health_url,
                    headers=self._profile.headers,
                ) as response:
                    response.raise_for_status()
            return HealthStatus(healthy=True, detail=self._model.alias)
        except Exception:
            return HealthStatus(
                healthy=False,
                detail=f"Model unavailable: {self._model.alias}",
            )

    async def _stream_openai(
        self,
        request: ProviderCallRequest,
        cancellation: asyncio.Event,
    ) -> AsyncIterator[ProviderChunk]:
        if cancellation.is_set():
            return
        messages = [dict(message) for message in request.messages]
        vision_error = self._profile.vision.validate(messages)
        if vision_error:
            yield ProviderChunk(
                finish_reason="error",
                terminal=True,
                error_code="unsupported_input",
                provider_model=self.model_alias,
            )
            return
        payload = build_chat_payload(
            self._model.model,
            messages,
            list(request.tools),
            request.temperature,
            request.max_output_tokens,
            cache_prompt=self._profile.cache_prompt,
            stream_options=self._profile.stream_options,
            thinking=(
                self._model.thinking.model_dump()
                if self._model.thinking is not None
                else None
            ),
        )
        payload["stream"] = True
        accumulator = OpenAIStreamAccumulator()
        emitted_terminal = False
        try:
            timeout = self._stream_timeout()
            async with self._client_factory(timeout=timeout) as client:
                async with client.stream(
                    "POST",
                    self._profile.chat_url,
                    json=payload,
                    headers=self._profile.headers,
                ) as response:
                    response.raise_for_status()
                    closer = asyncio.create_task(
                        self._close_on_cancel(response, cancellation)
                    )
                    try:
                        async for line in iter_limited_lines(
                            response,
                            max_line_bytes=_MAX_STREAM_LINE_BYTES,
                            max_total_bytes=_MAX_MODEL_STREAM_BYTES,
                        ):
                            if cancellation.is_set():
                                return
                            line = line.strip()
                            if not line or not line.startswith("data: "):
                                continue
                            data = line[6:]
                            if data == "[DONE]":
                                terminal = accumulator.finish()
                                yield self._terminal_chunk(
                                    terminal.finish_reason,
                                    terminal.delta_tool_calls,
                                    terminal.usage,
                                )
                                emitted_terminal = True
                                return
                            parsed = json.loads(data)
                            for chunk in accumulator.process_chunk(parsed):
                                if chunk.delta_content:
                                    yield ProviderChunk(
                                        content_delta=chunk.delta_content
                                    )
                                if chunk.delta_thinking:
                                    yield ProviderChunk(
                                        reasoning_delta=chunk.delta_thinking
                                    )
                    finally:
                        closer.cancel()
                        await asyncio.gather(closer, return_exceptions=True)
            if not emitted_terminal and accumulator.last_finish_reason:
                terminal = accumulator.finish()
                yield self._terminal_chunk(
                    terminal.finish_reason,
                    terminal.delta_tool_calls,
                    terminal.usage,
                )
                return
        except asyncio.CancelledError:
            raise
        except Exception:
            if cancellation.is_set():
                return
        if not emitted_terminal:
            yield ProviderChunk(
                finish_reason="error",
                terminal=True,
                error_code="provider_failed",
                provider_model=self.model_alias,
            )

    async def _stream_anthropic(
        self,
        request: ProviderCallRequest,
        cancellation: asyncio.Event,
    ) -> AsyncIterator[ProviderChunk]:
        if cancellation.is_set():
            return
        messages = [dict(message) for message in request.messages]
        vision_error = self._profile.vision.validate(messages)
        if vision_error:
            yield ProviderChunk(
                finish_reason="error",
                terminal=True,
                error_code="unsupported_input",
                provider_model=self.model_alias,
            )
            return
        payload = build_anthropic_payload(
            self._model.model,
            messages,
            list(request.tools),
            request.temperature,
            request.max_output_tokens,
        )
        payload["stream"] = True
        accumulator = AnthropicStreamAccumulator()
        tool_calls: list[dict[str, Any]] = []
        emitted_terminal = False
        current_event = ""
        try:
            timeout = self._stream_timeout()
            async with self._client_factory(timeout=timeout) as client:
                async with client.stream(
                    "POST",
                    self._profile.chat_url,
                    json=payload,
                    headers=self._profile.headers,
                ) as response:
                    response.raise_for_status()
                    closer = asyncio.create_task(
                        self._close_on_cancel(response, cancellation)
                    )
                    try:
                        async for line in iter_limited_lines(
                            response,
                            max_line_bytes=_MAX_STREAM_LINE_BYTES,
                            max_total_bytes=_MAX_MODEL_STREAM_BYTES,
                        ):
                            if cancellation.is_set():
                                return
                            line = line.strip()
                            if not line:
                                continue
                            if line.startswith("event: "):
                                current_event = line[7:]
                                continue
                            if not line.startswith("data: "):
                                continue
                            data = json.loads(line[6:])
                            event_type = str(data.get("type") or current_event)
                            for chunk in accumulator.process(event_type, data):
                                if chunk.delta_content:
                                    yield ProviderChunk(
                                        content_delta=chunk.delta_content
                                    )
                                if chunk.delta_thinking:
                                    yield ProviderChunk(
                                        reasoning_delta=chunk.delta_thinking
                                    )
                                if chunk.delta_tool_calls:
                                    tool_calls.extend(chunk.delta_tool_calls)
                                if chunk.finish_reason:
                                    yield self._terminal_chunk(
                                        chunk.finish_reason,
                                        tool_calls,
                                        chunk.usage,
                                    )
                                    emitted_terminal = True
                                    return
                    finally:
                        closer.cancel()
                        await asyncio.gather(closer, return_exceptions=True)
        except asyncio.CancelledError:
            raise
        except Exception:
            if cancellation.is_set():
                return
        if not emitted_terminal:
            yield ProviderChunk(
                finish_reason="error",
                terminal=True,
                error_code="provider_failed",
                provider_model=self.model_alias,
            )

    def _terminal_chunk(
        self,
        finish_reason: str,
        raw_tool_calls: list[dict[str, Any]],
        usage: dict[str, Any],
    ) -> ProviderChunk:
        normalized_reason = _finish_reason(finish_reason)
        try:
            calls = _tool_calls(raw_tool_calls)
        except (ValueError, TypeError, json.JSONDecodeError):
            return ProviderChunk(
                finish_reason="error",
                terminal=True,
                error_code="provider_protocol_error",
                provider_model=self.model_alias,
            )
        if calls and normalized_reason == "stop":
            normalized_reason = "tool_calls"
        return ProviderChunk(
            tool_calls=calls,
            finish_reason=normalized_reason,
            usage=usage,
            terminal=True,
            error_code=(
                "provider_failed" if normalized_reason == "error" else ""
            ),
            provider_model=self.model_alias,
        )

    def _stream_timeout(self) -> httpx.Timeout:
        return httpx.Timeout(
            connect=10.0,
            read=max(self._model.timeout * 2, 300.0),
            write=10.0,
            pool=10.0,
        )

    @staticmethod
    async def _close_on_cancel(response: Any, cancellation: asyncio.Event) -> None:
        await cancellation.wait()
        await response.aclose()


class FailoverModelProvider(ModelProviderPort):
    """Fallback only when the primary fails before emitting observable output."""

    def __init__(
        self,
        primary: ModelProviderPort,
        fallback: ModelProviderPort,
    ) -> None:
        self._primary = primary
        self._fallback = fallback

    def stream(
        self,
        request: ProviderCallRequest,
        cancellation: asyncio.Event,
    ) -> AsyncIterator[ProviderChunk]:
        return self._stream(request, cancellation)

    async def _stream(
        self,
        request: ProviderCallRequest,
        cancellation: asyncio.Event,
    ) -> AsyncIterator[ProviderChunk]:
        emitted = False
        terminal = False
        primary_model = ""
        try:
            async for chunk in self._primary.stream(request, cancellation):
                primary_model = chunk.provider_model or primary_model
                if chunk.terminal and chunk.finish_reason == "error":
                    if emitted:
                        yield chunk
                        return
                    break
                if chunk.content_delta or chunk.reasoning_delta or chunk.tool_calls:
                    emitted = True
                terminal = terminal or chunk.terminal
                yield chunk
        except asyncio.CancelledError:
            raise
        except Exception:
            pass

        if cancellation.is_set() or terminal:
            return
        if emitted:
            yield ProviderChunk(
                finish_reason="error",
                terminal=True,
                error_code="provider_failed",
                provider_model=primary_model,
            )
            return
        async for chunk in self._fallback.stream(request, cancellation):
            yield chunk.model_copy(update={"failover_used": True})
