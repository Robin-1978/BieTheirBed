from __future__ import annotations

import asyncio
import json
from typing import Any, AsyncGenerator

import httpx

from pc_assistant.model_adapter.parsers.anthropic import (
    AnthropicStreamAccumulator,
    build_anthropic_payload,
    convert_tools_to_anthropic,
    parse_anthropic_response,
)
from pc_assistant.model_adapter.parsers.openai import (
    OpenAIStreamAccumulator,
    apply_cache_control,
    build_chat_payload,
    parse_chat_response,
)
from pc_assistant.model_adapter.profiles import resolve_profile
from pc_assistant.model_adapter.retry import request_with_retry
from pc_assistant.model_adapter.types import (
    LLMMessage,
    LLMResponse,
    StreamChunk,
)

__all__ = ["LLMMessage", "LLMProvider", "FailoverLLMProvider", "LLMResponse", "StreamChunk"]


class LLMProvider:
    """Facade over provider-specific backends.

    Transport (httpx), retry and cancellation bookkeeping live here so the
    streaming loops can be aborted per session; payload building and response
    parsing are delegated to :mod:`pc_assistant.model_adapter`.
    """

    def __init__(
        self,
        server_url: str = "http://127.0.0.1:8080",
        model_name: str = "",
        timeout: float = 120.0,
        max_retries: int = 3,
        provider: str = "llamacpp",
        api_key: str = "",
        api_base: str = "",
        supports_vision: bool | None = None,
        thinking: dict[str, Any] | None = None,
    ) -> None:
        self._provider = provider
        self._api_key = api_key
        self._model_name = model_name
        self._timeout = timeout
        self._max_retries = max_retries
        self._thinking = dict(thinking) if thinking is not None else None
        self._cancelled = False
        self._session_cancel: dict[str, bool] = {}
        self._streams: dict[str, httpx.Response] = {}
        self._active_response: httpx.Response | None = None

        self._profile = resolve_profile(provider, server_url, api_key, api_base, supports_vision=supports_vision)
        self._server_url = self._profile.server_url
        self._headers = self._profile.headers

    @property
    def supports_vision(self) -> bool:
        return self._profile.supports_vision

    @property
    def vision_capabilities(self):
        return self._profile.vision

    def _vision_error(self, messages: list[dict[str, Any]]) -> str:
        return self._profile.vision.validate(messages)

    async def _request_with_retry(
        self,
        client: httpx.AsyncClient,
        method: str,
        url: str,
        **kwargs: Any,
    ) -> httpx.Response:
        return await request_with_retry(self._max_retries, client, method, url, **kwargs)

    def _build_anthropic_payload(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None, temperature: float = 0.7, max_tokens: int = 1024, cache_control: dict[str, Any] | None = None) -> dict[str, Any]:
        return build_anthropic_payload(self._model_name, messages, tools, temperature, max_tokens, cache_control)

    def _convert_tools_to_anthropic(self, tools: list[dict[str, Any]], cache_control: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        return convert_tools_to_anthropic(tools, cache_control)

    def _parse_anthropic_response(self, data: dict[str, Any]) -> LLMResponse:
        return parse_anthropic_response(data)

    async def _chat_anthropic(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None, temperature: float, max_tokens: int, cache_control: dict[str, Any] | None = None) -> LLMResponse:
        payload = self._build_anthropic_payload(messages, tools, temperature, max_tokens, cache_control)
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await self._request_with_retry(
                    client, "POST", self._profile.chat_url, json=payload, headers=self._headers,
                )
                data = resp.json()
        except httpx.HTTPError as e:
            return LLMResponse(content=f"LLM request failed: {e}", finish_reason="error")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            return LLMResponse(content=f"LLM request failed: {e}", finish_reason="error")
        return self._parse_anthropic_response(data)

    async def _chat_stream_anthropic(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.7,
        max_tokens: int = 1024,
        tool_choice: str | dict[str, Any] | None = None,
        cancel_key: str | None = None,
        cache_control: dict[str, Any] | None = None,
    ) -> AsyncGenerator[StreamChunk, None]:
        payload = self._build_anthropic_payload(messages, tools, temperature, max_tokens, cache_control)
        payload["stream"] = True
        if tool_choice is not None:
            payload["tool_choice"] = tool_choice

        stream_timeout = httpx.Timeout(
            connect=10.0,
            read=max(self._timeout * 2, 300.0),
            write=10.0,
            pool=10.0,
        )

        accumulator = AnthropicStreamAccumulator()

        try:
            async with httpx.AsyncClient(timeout=stream_timeout) as client:
                async with client.stream(
                    "POST",
                    self._profile.chat_url,
                    json=payload,
                    headers=self._headers,
                ) as response:
                    self._active_response = response
                    if cancel_key:
                        self._streams[cancel_key] = response
                    try:
                        response.raise_for_status()
                        current_event = ""
                        async for line in response.aiter_lines():
                            if self._cancelled or (cancel_key and self._session_cancel.get(cancel_key)):
                                await response.aclose()
                                return

                            line = line.strip()
                            if not line:
                                continue
                            if line.startswith("event: "):
                                current_event = line[len("event: "):]
                                continue
                            if not line.startswith("data: "):
                                continue
                            data_str = line[len("data: "):]

                            try:
                                data = json.loads(data_str)
                            except json.JSONDecodeError:
                                continue

                            event_type = data.get("type", current_event)

                            for chunk in accumulator.process(event_type, data):
                                yield chunk

                            if event_type == "message_stop":
                                return
                    finally:
                        self._active_response = None
                        if cancel_key:
                            self._streams.pop(cancel_key, None)

        except httpx.HTTPError as e:
            if self._cancelled or (cancel_key and self._session_cancel.get(cancel_key)):
                return
            yield StreamChunk(
                delta_content=f"LLM stream failed: {e}",
                finish_reason="error",
            )
        except asyncio.CancelledError:
            raise
        except Exception as e:
            if self._cancelled or (cancel_key and self._session_cancel.get(cancel_key)):
                return
            error_detail = str(e) if str(e) else type(e).__name__
            yield StreamChunk(
                delta_content=f"LLM stream failed: {error_detail}",
                finish_reason="error",
            )

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.7,
        max_tokens: int = 1024,
        tool_choice: str | dict[str, Any] | None = None,
        cache_control: dict[str, Any] | None = None,
    ) -> LLMResponse:
        if error := self._vision_error(messages):
            return LLMResponse(content=f"LLM request failed: {error}", finish_reason="error")
        if self._provider == "anthropic":
            return await self._chat_anthropic(messages, tools, temperature, max_tokens, cache_control)

        payload = build_chat_payload(
            self._model_name,
            messages,
            tools,
            temperature,
            max_tokens,
            tool_choice,
            cache_prompt=self._profile.cache_prompt,
            stream_options=False,
            thinking=self._thinking,
        )
        apply_cache_control(payload["messages"], cache_control)
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await self._request_with_retry(
                    client, "POST", self._profile.chat_url, json=payload, headers=self._headers,
                )
                data = resp.json()
        except httpx.HTTPError as e:
            return LLMResponse(content=f"LLM request failed: {e}", finish_reason="error")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            return LLMResponse(content=f"LLM request failed: {e}", finish_reason="error")
        return parse_chat_response(data)

    async def chat_stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.7,
        max_tokens: int = 1024,
        tool_choice: str | dict[str, Any] | None = None,
        cancel_key: str | None = None,
        cache_control: dict[str, Any] | None = None,
    ) -> AsyncGenerator[StreamChunk, None]:
        self._cancelled = False
        if cancel_key:
            self._session_cancel.pop(cancel_key, None)

        if error := self._vision_error(messages):
            yield StreamChunk(delta_content=f"LLM stream failed: {error}", finish_reason="error")
            return

        if self._provider == "anthropic":
            async for chunk in self._chat_stream_anthropic(
                messages, tools, temperature, max_tokens, tool_choice, cancel_key, cache_control,
            ):
                yield chunk
            return

        payload = build_chat_payload(
            self._model_name,
            messages,
            tools,
            temperature,
            max_tokens,
            tool_choice,
            cache_prompt=self._profile.cache_prompt,
            stream_options=True,
            thinking=self._thinking,
        )
        payload["stream"] = True
        apply_cache_control(payload["messages"], cache_control)

        accumulator = OpenAIStreamAccumulator()

        try:
            stream_timeout = httpx.Timeout(
                connect=10.0,
                read=max(self._timeout * 2, 300.0),
                write=10.0,
                pool=10.0,
            )
            async with httpx.AsyncClient(timeout=stream_timeout) as client:
                async with client.stream(
                    "POST",
                    self._profile.chat_url,
                    json=payload,
                    headers=self._headers,
                ) as response:
                    self._active_response = response
                    if cancel_key:
                        self._streams[cancel_key] = response
                    try:
                        response.raise_for_status()
                        async for line in response.aiter_lines():
                            if self._cancelled or (cancel_key and self._session_cancel.get(cancel_key)):
                                await response.aclose()
                                return

                            line = line.strip()
                            if not line:
                                continue
                            if not line.startswith("data: "):
                                continue
                            data_str = line[len("data: "):]
                            if data_str == "[DONE]":
                                yield accumulator.finish()
                                return

                            try:
                                chunk_data = json.loads(data_str)
                            except json.JSONDecodeError:
                                continue

                            for chunk in accumulator.process_chunk(chunk_data):
                                yield chunk
                    finally:
                        self._active_response = None
                        if cancel_key:
                            self._streams.pop(cancel_key, None)
        except httpx.HTTPError as e:
            if self._cancelled or (cancel_key and self._session_cancel.get(cancel_key)):
                return
            yield StreamChunk(
                delta_content=f"LLM stream failed: {e}",
                delta_tool_calls=[],
                finish_reason="error",
            )
        except asyncio.CancelledError:
            raise
        except Exception as e:
            if self._cancelled or (cancel_key and self._session_cancel.get(cancel_key)):
                return
            error_detail = str(e) if str(e) else type(e).__name__
            yield StreamChunk(
                delta_content=f"LLM stream failed: {error_detail}",
                delta_tool_calls=[],
                finish_reason="error",
            )

    def cancel(self, session_id: str | None = None) -> None:
        if session_id:
            self._session_cancel[session_id] = True
            responses = [self._streams.get(session_id)]
        else:
            self._cancelled = True
            responses = [self._active_response, *self._streams.values()]
        seen: set[int] = set()
        for response in responses:
            if response is None or response.is_closed or id(response) in seen:
                continue
            seen.add(id(response))
            try:
                asyncio.get_running_loop().create_task(self._abort_response(response))
            except RuntimeError:
                pass

    async def _abort_response(self, response: httpx.Response) -> None:
        """Force-close an in-flight streaming response so a blocked
        ``aiter_lines()`` wakes up immediately instead of waiting for the
        next SSE chunk (or the read timeout)."""
        try:
            await response.aclose()
        except Exception:
            pass

    def reset_cancelled(self, session_id: str | None = None) -> None:
        self._cancelled = False
        if session_id:
            self._session_cancel.pop(session_id, None)

    async def health_check(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(self._profile.health_url, headers=self._headers)
                return resp.status_code == 200
        except httpx.HTTPError:
            return False


class FailoverLLMProvider:
    """Try a primary provider, then a local provider when it fails."""

    def __init__(self, primary: LLMProvider, fallback: LLMProvider | None = None) -> None:
        self.primary = primary
        self.fallback = fallback

    @property
    def supports_vision(self) -> bool:
        # Prompt preparation must match the provider that receives the first
        # request. Advertising fallback-only vision here made Agent hydrate
        # image references into a text-only primary request, so the model could
        # silently miss the image instead of routing through image_inspect.
        return self.primary.supports_vision

    @property
    def vision_capabilities(self):
        return self.primary.vision_capabilities

    async def chat(self, *args: Any, **kwargs: Any) -> LLMResponse:
        response = await self.primary.chat(*args, **kwargs)
        if response.finish_reason != "error" or self.fallback is None:
            return response
        return await self.fallback.chat(*args, **kwargs)

    async def chat_stream(self, *args: Any, **kwargs: Any) -> AsyncGenerator[StreamChunk, None]:
        if self.fallback is None:
            async for chunk in self.primary.chat_stream(*args, **kwargs):
                yield chunk
            return

        emitted = False
        failed = False
        error_chunk: StreamChunk | None = None
        async for chunk in self.primary.chat_stream(*args, **kwargs):
            if chunk.finish_reason == "error":
                failed = True
                error_chunk = chunk
                break
            if chunk.delta_content or chunk.delta_thinking or chunk.delta_tool_calls:
                emitted = True
            yield chunk

        # Do not replay a turn after partial primary output; that could duplicate
        # text or tool calls. Fallback is intended for failures before output.
        if failed and not emitted:
            async for chunk in self.fallback.chat_stream(*args, **kwargs):
                yield chunk
        elif error_chunk is not None:
            yield error_chunk

    def cancel(self, session_id: str | None = None) -> None:
        self.primary.cancel(session_id)
        if self.fallback:
            self.fallback.cancel(session_id)

    def reset_cancelled(self, session_id: str | None = None) -> None:
        self.primary.reset_cancelled(session_id)
        if self.fallback:
            self.fallback.reset_cancelled(session_id)

    async def health_check(self) -> bool:
        return await self.primary.health_check()
