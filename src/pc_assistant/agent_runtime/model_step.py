"""One provider-neutral, observable model invocation."""
from __future__ import annotations

import asyncio
import json
import time
import uuid
from collections.abc import AsyncIterator
from typing import Any, Literal, Protocol

from pydantic import Field, model_validator

from pc_assistant.agent_runtime.contracts import ContractModel, RuntimeScope
from pc_assistant.agent_runtime.tool_step import ProposedToolCall
from pc_assistant.context.assembly import truncate_messages
from pc_assistant.context.token_estimate import TokenEstimator


ModelPurpose = Literal["react", "reflection"]


class ProviderChunk(ContractModel):
    content_delta: str = ""
    reasoning_delta: str = ""
    tool_calls: tuple[ProposedToolCall, ...] = ()
    finish_reason: Literal["", "stop", "tool_calls", "length", "error"] = ""
    usage: dict[str, Any] = Field(default_factory=dict)
    terminal: bool = False
    error_code: str = ""
    provider_model: str = ""
    failover_used: bool = False

    @model_validator(mode="after")
    def validate_terminal_fields(self) -> ProviderChunk:
        if self.tool_calls and not self.terminal:
            raise ValueError("Tool calls are allowed only on a terminal provider chunk")
        if self.terminal and not self.finish_reason:
            raise ValueError("Terminal provider chunk requires a finish reason")
        if self.finish_reason == "error" and not self.terminal:
            raise ValueError("Provider error must be terminal")
        return self


class ProviderCallRequest(ContractModel):
    call_id: str
    purpose: ModelPurpose
    messages: tuple[dict[str, Any], ...]
    tools: tuple[dict[str, Any], ...] = ()
    temperature: float = Field(default=0.2, ge=0.0, le=2.0)
    max_output_tokens: int = Field(default=1024, gt=0)


class ModelProviderPort(Protocol):
    def stream(
        self,
        request: ProviderCallRequest,
        cancellation: asyncio.Event,
    ) -> AsyncIterator[ProviderChunk]: ...


class MessageHydratorPort(Protocol):
    async def hydrate(
        self,
        scope: RuntimeScope,
        messages: list[dict[str, Any]],
    ) -> list[dict[str, Any]]: ...


class IdentityMessageHydrator:
    async def hydrate(
        self,
        scope: RuntimeScope,
        messages: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        del scope
        return messages


class ModelStepRequest(ContractModel):
    scope: RuntimeScope
    purpose: ModelPurpose = "react"
    messages: tuple[dict[str, Any], ...]
    system_prompt: str = ""
    runtime_context: str = ""
    tools: tuple[dict[str, Any], ...] = ()
    prompt_budget: int = Field(default=8192, gt=256)
    max_output_tokens: int = Field(default=1024, gt=0)
    temperature: float = Field(default=0.2, ge=0.0, le=2.0)


class ModelStepResult(ContractModel):
    status: Literal["completed", "failed", "cancelled"]
    content: str = ""
    reasoning: str = ""
    tool_calls: tuple[ProposedToolCall, ...] = ()
    finish_reason: str = ""
    usage: dict[str, Any] = Field(default_factory=dict)
    prompt_tokens_estimated: int = 0
    schema_tokens_estimated: int = 0
    latency_ms: float = 0.0
    ttft_ms: float = 0.0
    error_code: str = ""
    provider_model: str = ""
    failover_used: bool = False


class ModelStepEvent(ContractModel):
    event_type: Literal["content_delta", "reasoning_delta", "terminal"]
    content: str = ""
    result: ModelStepResult | None = None

    @model_validator(mode="after")
    def validate_result(self) -> ModelStepEvent:
        if (self.event_type == "terminal") != (self.result is not None):
            raise ValueError("Only terminal model events carry a result")
        return self


class ModelStep:
    def __init__(
        self,
        provider: ModelProviderPort,
        *,
        token_estimator: TokenEstimator | None = None,
        message_hydrator: MessageHydratorPort | None = None,
        call_id_factory=None,
        clock=time.monotonic,
    ) -> None:
        self._provider = provider
        self._tokens = token_estimator or TokenEstimator()
        self._message_hydrator = message_hydrator or IdentityMessageHydrator()
        self._call_id_factory = call_id_factory or (lambda: uuid.uuid4().hex)
        self._clock = clock

    async def run(
        self,
        request: ModelStepRequest,
        cancellation: asyncio.Event,
    ) -> AsyncIterator[ModelStepEvent]:
        started = self._clock()
        tool_tokens = self._tokens.text_tokens(
            json.dumps(request.tools, ensure_ascii=False, sort_keys=True)
        )
        if tool_tokens > request.prompt_budget - 256:
            yield self._terminal(
                "failed",
                started,
                None,
                tool_tokens,
                tool_tokens,
                error_code="context_budget_exceeded",
            )
            return
        message_budget = max(256, request.prompt_budget - tool_tokens)
        ephemeral_messages = list(request.messages)
        if request.runtime_context:
            insertion = len(ephemeral_messages)
            if ephemeral_messages and ephemeral_messages[-1].get("role") == "user":
                insertion -= 1
            ephemeral_messages.insert(
                insertion,
                {"role": "user", "content": request.runtime_context},
            )
        if request.system_prompt:
            ephemeral_messages.insert(
                0,
                {"role": "system", "content": request.system_prompt},
            )
        hydrated = await self._message_hydrator.hydrate(
            request.scope,
            ephemeral_messages,
        )
        messages = truncate_messages(
            hydrated,
            budget=message_budget,
        )
        prompt_tokens = self._tokens.messages_tokens(messages) + tool_tokens
        call = ProviderCallRequest(
            call_id=self._call_id_factory(),
            purpose=request.purpose,
            messages=tuple(messages),
            tools=request.tools,
            temperature=request.temperature,
            max_output_tokens=request.max_output_tokens,
        )
        first_output_at: float | None = None
        content_parts: list[str] = []
        reasoning_parts: list[str] = []
        terminal_chunk: ProviderChunk | None = None

        if cancellation.is_set():
            yield self._terminal(
                "cancelled",
                started,
                first_output_at,
                prompt_tokens,
                tool_tokens,
                error_code="cancelled",
            )
            return
        try:
            async for chunk in self._provider.stream(call, cancellation):
                if cancellation.is_set():
                    yield self._terminal(
                        "cancelled",
                        started,
                        first_output_at,
                        prompt_tokens,
                        tool_tokens,
                        content="".join(content_parts),
                        reasoning="".join(reasoning_parts),
                        error_code="cancelled",
                    )
                    return
                if chunk.content_delta:
                    first_output_at = first_output_at or self._clock()
                    content_parts.append(chunk.content_delta)
                    yield ModelStepEvent(
                        event_type="content_delta",
                        content=chunk.content_delta,
                    )
                if chunk.reasoning_delta:
                    first_output_at = first_output_at or self._clock()
                    reasoning_parts.append(chunk.reasoning_delta)
                    yield ModelStepEvent(
                        event_type="reasoning_delta",
                        content=chunk.reasoning_delta,
                    )
                if chunk.terminal:
                    if terminal_chunk is not None:
                        raise RuntimeError("Provider emitted multiple terminal chunks")
                    terminal_chunk = chunk
        except asyncio.CancelledError:
            raise
        except Exception:
            yield self._terminal(
                "failed",
                started,
                first_output_at,
                prompt_tokens,
                tool_tokens,
                content="".join(content_parts),
                reasoning="".join(reasoning_parts),
                error_code="provider_failed",
            )
            return

        if cancellation.is_set():
            status: Literal["completed", "failed", "cancelled"] = "cancelled"
            error_code = "cancelled"
        elif terminal_chunk is None or terminal_chunk.finish_reason == "error":
            status = "failed"
            error_code = terminal_chunk.error_code if terminal_chunk else "provider_failed"
            error_code = error_code or "provider_failed"
        else:
            status = "completed"
            error_code = ""
        yield self._terminal(
            status,
            started,
            first_output_at,
            prompt_tokens,
            tool_tokens,
            content="".join(content_parts),
            reasoning="".join(reasoning_parts),
            tool_calls=terminal_chunk.tool_calls if terminal_chunk else (),
            finish_reason=terminal_chunk.finish_reason if terminal_chunk else "",
            usage=terminal_chunk.usage if terminal_chunk else {},
            error_code=error_code,
            provider_model=terminal_chunk.provider_model if terminal_chunk else "",
            failover_used=terminal_chunk.failover_used if terminal_chunk else False,
        )

    def _terminal(
        self,
        status: Literal["completed", "failed", "cancelled"],
        started: float,
        first_output_at: float | None,
        prompt_tokens: int,
        schema_tokens: int,
        *,
        content: str = "",
        reasoning: str = "",
        tool_calls: tuple[ProposedToolCall, ...] = (),
        finish_reason: str = "",
        usage: dict[str, Any] | None = None,
        error_code: str = "",
        provider_model: str = "",
        failover_used: bool = False,
    ) -> ModelStepEvent:
        finished = self._clock()
        return ModelStepEvent(
            event_type="terminal",
            result=ModelStepResult(
                status=status,
                content=content,
                reasoning=reasoning,
                tool_calls=tool_calls,
                finish_reason=finish_reason,
                usage=usage or {},
                prompt_tokens_estimated=prompt_tokens,
                schema_tokens_estimated=schema_tokens,
                latency_ms=max(0.0, (finished - started) * 1000),
                ttft_ms=(
                    max(0.0, (first_output_at - started) * 1000)
                    if first_output_at is not None
                    else 0.0
                ),
                error_code=error_code,
                provider_model=provider_model,
                failover_used=failover_used,
            ),
        )
