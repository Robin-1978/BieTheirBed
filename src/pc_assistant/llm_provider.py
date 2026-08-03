from __future__ import annotations

import asyncio
import json
from typing import Any, AsyncGenerator

import httpx
from pydantic import BaseModel


class LLMMessage(BaseModel):
    role: str
    content: str


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


class LLMProvider:
    def __init__(
        self,
        server_url: str = "http://127.0.0.1:8080",
        model_name: str = "",
        timeout: float = 120.0,
        max_retries: int = 3,
        provider: str = "llamacpp",
        api_key: str = "",
        api_base: str = "",
    ) -> None:
        self._provider = provider
        self._api_key = api_key
        self._model_name = model_name
        self._timeout = timeout
        self._max_retries = max_retries
        self._cancelled = False
        self._active_response: httpx.Response | None = None

        if provider == "openai":
            self._server_url = "https://api.openai.com/v1"
            self._headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        elif provider == "anthropic":
            self._server_url = "https://api.anthropic.com"
            self._headers = {"x-api-key": api_key, "anthropic-version": "2023-06-01"} if api_key else {}
        elif provider == "openai_compatible":
            self._server_url = (api_base or server_url).rstrip("/")
            self._headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        else:
            self._server_url = server_url.rstrip("/")
            self._headers = {}

    async def _request_with_retry(
        self,
        client: httpx.AsyncClient,
        method: str,
        url: str,
        **kwargs: Any,
    ) -> httpx.Response:
        last_error: Exception = RuntimeError("Request failed with no error information")
        for attempt in range(self._max_retries):
            try:
                if method == "POST":
                    resp = await client.post(url, **kwargs)
                else:
                    resp = await client.get(url, **kwargs)
                resp.raise_for_status()
                return resp
            except asyncio.CancelledError:
                raise
            except (httpx.ConnectError, httpx.ReadError, httpx.WriteError, httpx.PoolTimeout) as e:
                last_error = e
                if attempt < self._max_retries - 1:
                    delay = 0.5 * (2 ** attempt)
                    await asyncio.sleep(delay)
            except httpx.HTTPStatusError as e:
                if e.response.status_code in (429, 500, 502, 503, 504):
                    last_error = e
                    if attempt < self._max_retries - 1:
                        delay = 0.5 * (2 ** attempt)
                        await asyncio.sleep(delay)
                else:
                    raise
        raise last_error  # type: ignore[misc]

    def _build_anthropic_payload(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None, temperature: float = 0.7, max_tokens: int = 1024) -> dict[str, Any]:
        system_msg = ""
        filtered_msgs: list[dict[str, Any]] = []
        for m in messages:
            if m["role"] == "system":
                system_msg += m["content"] + "\n"
            else:
                filtered_msgs.append(m)

        payload: dict[str, Any] = {
            "model": self._model_name,
            "messages": filtered_msgs,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if system_msg:
            payload["system"] = system_msg.strip()
        if tools:
            payload["tools"] = self._convert_tools_to_anthropic(tools)
        return payload

    def _convert_tools_to_anthropic(self, tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
        anthropic_tools: list[dict[str, Any]] = []
        for t in tools:
            func = t.get("function", {})
            anthropic_tools.append({
                "name": func.get("name", ""),
                "description": func.get("description", ""),
                "input_schema": func.get("parameters", {"type": "object", "properties": {}}),
            })
        return anthropic_tools

    def _parse_anthropic_response(self, data: dict[str, Any]) -> LLMResponse:
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

    async def _chat_anthropic(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None, temperature: float, max_tokens: int) -> LLMResponse:
        payload = self._build_anthropic_payload(messages, tools, temperature, max_tokens)
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await self._request_with_retry(
                    client, "POST", f"{self._server_url}/v1/messages", json=payload, headers=self._headers,
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
    ) -> AsyncGenerator[StreamChunk, None]:
        payload = self._build_anthropic_payload(messages, tools, temperature, max_tokens)
        payload["stream"] = True
        if tool_choice is not None:
            payload["tool_choice"] = tool_choice

        stream_timeout = httpx.Timeout(
            connect=10.0,
            read=max(self._timeout * 2, 300.0),
            write=10.0,
            pool=10.0,
        )

        content_blocks: dict[int, dict[str, Any]] = {}
        last_stop_reason = ""
        last_usage: dict[str, Any] = {}

        try:
            async with httpx.AsyncClient(timeout=stream_timeout) as client:
                async with client.stream(
                    "POST",
                    f"{self._server_url}/v1/messages",
                    json=payload,
                    headers=self._headers,
                ) as response:
                    self._active_response = response
                    try:
                        response.raise_for_status()
                        current_event = ""
                        async for line in response.aiter_lines():
                            if self._cancelled:
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

                            if event_type == "message_start":
                                msg = data.get("message", {})
                                last_usage = msg.get("usage", {})

                            elif event_type == "content_block_start":
                                idx = data.get("index", 0)
                                block = data.get("content_block", {})
                                content_blocks[idx] = block

                            elif event_type == "content_block_delta":
                                idx = data.get("index", 0)
                                delta = data.get("delta", {})
                                delta_type = delta.get("type", "")
                                block = content_blocks.setdefault(idx, {"type": delta_type})

                                if delta_type == "text_delta":
                                    text = delta.get("text", "")
                                    block["text"] = block.get("text", "") + text
                                    yield StreamChunk(delta_content=text)

                                elif delta_type == "input_json_delta":
                                    partial = delta.get("partial_json", "")
                                    block["partial_json"] = block.get("partial_json", "") + partial

                            elif event_type == "content_block_stop":
                                idx = data.get("index", 0)
                                block = content_blocks.get(idx, {})

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
                                    yield StreamChunk(delta_tool_calls=[tool_call])

                            elif event_type == "message_delta":
                                delta = data.get("delta", {})
                                last_stop_reason = delta.get("stop_reason", "")
                                usage = data.get("usage", {})
                                if usage:
                                    last_usage = {**last_usage, **usage}

                            elif event_type == "message_stop":
                                yield StreamChunk(
                                    finish_reason=last_stop_reason,
                                    usage=last_usage,
                                )
                                return
                    finally:
                        self._active_response = None

        except httpx.HTTPError as e:
            if self._cancelled:
                return
            yield StreamChunk(
                delta_content=f"LLM stream failed: {e}",
                finish_reason="error",
            )
        except asyncio.CancelledError:
            raise
        except Exception as e:
            if self._cancelled:
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
    ) -> LLMResponse:
        if self._provider == "anthropic":
            return await self._chat_anthropic(messages, tools, temperature, max_tokens)

        payload: dict[str, Any] = {
            "model": self._model_name,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if tools:
            payload["tools"] = tools
        if tool_choice is not None:
            payload["tool_choice"] = tool_choice
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await self._request_with_retry(
                    client, "POST", f"{self._server_url}/v1/chat/completions", json=payload, headers=self._headers,
                )
                data = resp.json()
        except httpx.HTTPError as e:
            return LLMResponse(content=f"LLM request failed: {e}", finish_reason="error")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            return LLMResponse(content=f"LLM request failed: {e}", finish_reason="error")
        choice = data.get("choices", [{}])[0]
        message = choice.get("message", {})
        content = message.get("content", "") or ""
        raw_tool_calls = message.get("tool_calls", [])
        tool_calls = self._normalize_tool_calls(raw_tool_calls)
        finish_reason = choice.get("finish_reason", "")
        usage = data.get("usage", {})
        return LLMResponse(
            content=content,
            tool_calls=tool_calls,
            finish_reason=finish_reason,
            usage=usage,
        )

    async def chat_stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.7,
        max_tokens: int = 1024,
        tool_choice: str | dict[str, Any] | None = None,
    ) -> AsyncGenerator[StreamChunk, None]:
        self._cancelled = False

        if self._provider == "anthropic":
            async for chunk in self._chat_stream_anthropic(messages, tools, temperature, max_tokens, tool_choice):
                yield chunk
            return

        payload: dict[str, Any] = {
            "model": self._model_name,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if tools:
            payload["tools"] = tools
        if tool_choice is not None:
            payload["tool_choice"] = tool_choice

        accumulated_tool_calls: dict[int, dict[str, Any]] = {}
        last_finish_reason = ""
        last_usage: dict[str, Any] = {}

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
                    f"{self._server_url}/v1/chat/completions",
                    json=payload,
                    headers=self._headers,
                ) as response:
                    self._active_response = response
                    try:
                        response.raise_for_status()
                        async for line in response.aiter_lines():
                            if self._cancelled:
                                await response.aclose()
                                return

                            line = line.strip()
                            if not line:
                                continue
                            if not line.startswith("data: "):
                                continue
                            data_str = line[len("data: "):]
                            if data_str == "[DONE]":
                                if accumulated_tool_calls:
                                    final_tool_calls = list(accumulated_tool_calls.values())
                                    for tc in final_tool_calls:
                                        if "function" in tc and isinstance(tc["function"].get("arguments"), str):
                                            try:
                                                tc["function"]["arguments"] = json.loads(tc["function"]["arguments"])
                                            except (json.JSONDecodeError, TypeError):
                                                pass
                                    yield StreamChunk(
                                        delta_content="",
                                        delta_tool_calls=final_tool_calls,
                                        finish_reason=last_finish_reason,
                                        usage=last_usage,
                                    )
                                else:
                                    yield StreamChunk(
                                        delta_content="",
                                        delta_tool_calls=[],
                                        finish_reason=last_finish_reason,
                                        usage=last_usage,
                                    )
                                return

                            try:
                                chunk_data = json.loads(data_str)
                            except json.JSONDecodeError:
                                continue

                            chunk_usage = chunk_data.get("usage")
                            if chunk_usage and isinstance(chunk_usage, dict):
                                last_usage = chunk_usage

                            choices = chunk_data.get("choices", [])
                            if not choices:
                                continue
                            delta = choices[0].get("delta", {})
                            finish_reason = choices[0].get("finish_reason", "")
                            if finish_reason:
                                last_finish_reason = finish_reason

                            delta_content = delta.get("content", "") or ""
                            delta_thinking = delta.get("reasoning_content", "") or delta.get("thinking", "") or ""

                            delta_tool_call_list = delta.get("tool_calls", [])
                            delta_tool_calls: list[dict[str, Any]] = []

                            for dtc in delta_tool_call_list:
                                idx = dtc.get("index", 0)
                                if idx not in accumulated_tool_calls:
                                    accumulated_tool_calls[idx] = {
                                        "id": dtc.get("id", ""),
                                        "type": "function",
                                        "function": {
                                            "name": "",
                                            "arguments": "",
                                        },
                                    }
                                acc = accumulated_tool_calls[idx]
                                if dtc.get("id"):
                                    acc["id"] = dtc["id"]
                                func_delta = dtc.get("function", {})
                                if func_delta.get("name"):
                                    acc["function"]["name"] += func_delta["name"]
                                if func_delta.get("arguments"):
                                    acc["function"]["arguments"] += func_delta["arguments"]

                            if delta_content or delta_thinking or delta_tool_call_list:
                                yield StreamChunk(
                                    delta_content=delta_content,
                                    delta_thinking=delta_thinking,
                                    delta_tool_calls=delta_tool_calls,
                                    finish_reason="",
                                )
                    finally:
                        self._active_response = None
        except httpx.HTTPError as e:
            if self._cancelled:
                return
            yield StreamChunk(
                delta_content=f"LLM stream failed: {e}",
                delta_tool_calls=[],
                finish_reason="error",
            )
        except asyncio.CancelledError:
            raise
        except Exception as e:
            if self._cancelled:
                return
            error_detail = str(e) if str(e) else type(e).__name__
            yield StreamChunk(
                delta_content=f"LLM stream failed: {error_detail}",
                delta_tool_calls=[],
                finish_reason="error",
            )

    def _normalize_tool_calls(self, raw_tool_calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
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

    @staticmethod
    def format_tool_result_message(tool_call_id: str, content: str) -> dict[str, Any]:
        return {
            "role": "tool",
            "tool_call_id": tool_call_id,
            "content": content,
        }

    def cancel(self) -> None:
        self._cancelled = True
        response = self._active_response
        if response is not None and not response.is_closed:
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

    def reset_cancelled(self) -> None:
        self._cancelled = False

    async def health_check(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                if self._provider == "anthropic":
                    resp = await client.get(f"{self._server_url}/", headers=self._headers)
                    return resp.status_code == 200
                resp = await client.get(f"{self._server_url}/v1/models", headers=self._headers)
                return resp.status_code == 200
        except httpx.HTTPError:
            return False
