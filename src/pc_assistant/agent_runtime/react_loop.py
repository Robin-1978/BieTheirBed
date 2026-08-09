"""Request-local Reasoning -> Acting -> Observation loop."""
from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import Field, model_validator

from pc_assistant.agent_runtime.contracts import (
    ContractModel,
    RuntimeEvent,
    RuntimeEventPayload,
    RuntimeScope,
)
from pc_assistant.agent_runtime.model_step import (
    ModelStep,
    ModelStepRequest,
    ModelStepResult,
)
from pc_assistant.agent_runtime.tool_step import (
    ConfirmationPort,
    ProposedToolCall,
    ToolStep,
    ToolStepContext,
    ToolStepResult,
    ToolCommitPort,
)
from pc_assistant.artifacts import ArtifactRef
from pc_assistant.tools.base import ToolCapability


class ReActLimits(ContractModel):
    max_iterations: int = Field(default=12, gt=0)
    max_tool_calls: int = Field(default=50, gt=0)


@dataclass(frozen=True)
class ReActContext:
    scope: RuntimeScope
    client_request_id: str
    messages: tuple[dict[str, Any], ...]
    tool_definitions: tuple[dict[str, Any], ...]
    capabilities: frozenset[ToolCapability]
    cancellation: asyncio.Event
    tool_definition_provider: Callable[[], tuple[dict[str, Any], ...]] | None = None
    run_id: str = ""
    confirmation: ConfirmationPort | None = None
    tool_commit: ToolCommitPort | None = None
    system_prompt: str = ""
    runtime_context: str = ""
    prompt_budget: int = 8192
    max_output_tokens: int = 1024
    temperature: float = 0.2


class ReActOutcome(ContractModel):
    status: Literal["completed", "failed", "cancelled"]
    messages: tuple[dict[str, Any], ...]
    final_content: str = ""
    iterations: int = Field(default=0, ge=0)
    tool_calls: int = 0
    error_code: str = ""


class ReActEvent(ContractModel):
    event_type: Literal["runtime_event", "outcome"]
    runtime_event: RuntimeEvent | None = None
    outcome: ReActOutcome | None = None

    @model_validator(mode="after")
    def validate_payload(self) -> ReActEvent:
        if self.event_type == "runtime_event":
            if self.runtime_event is None or self.outcome is not None:
                raise ValueError("Runtime ReAct event requires only runtime_event")
        elif self.outcome is None or self.runtime_event is not None:
            raise ValueError("Outcome ReAct event requires only outcome")
        return self


class ReActLoop:
    def __init__(
        self,
        model_step: ModelStep,
        tool_step: ToolStep,
        *,
        limits: ReActLimits | None = None,
        model_observer: Callable[
            [ReActContext, int, ModelStepResult], Awaitable[None]
        ]
        | None = None,
    ) -> None:
        self._model_step = model_step
        self._tool_step = tool_step
        self._limits = limits or ReActLimits()
        self._model_observer = model_observer

    async def run(self, context: ReActContext):
        messages = [dict(message) for message in context.messages]
        tool_call_count = 0

        for iteration in range(1, self._limits.max_iterations + 1):
            if context.cancellation.is_set():
                yield self._outcome(
                    "cancelled",
                    messages,
                    tool_call_count,
                    iterations=iteration - 1,
                    error_code="cancelled",
                )
                return

            model_result: ModelStepResult | None = None
            tool_definitions = (
                context.tool_definition_provider()
                if context.tool_definition_provider is not None
                else context.tool_definitions
            )
            async for event in self._model_step.run(
                ModelStepRequest(
                    scope=context.scope,
                    messages=tuple(messages),
                    system_prompt=context.system_prompt,
                    runtime_context=context.runtime_context,
                    tools=tool_definitions,
                    prompt_budget=context.prompt_budget,
                    max_output_tokens=context.max_output_tokens,
                    temperature=context.temperature,
                ),
                context.cancellation,
            ):
                if event.event_type in {"content_delta", "reasoning_delta"}:
                    yield ReActEvent(
                        event_type="runtime_event",
                        runtime_event=RuntimeEvent(
                            event_type=event.event_type,
                            payload=RuntimeEventPayload(
                                content=event.content,
                                iteration=iteration,
                            ),
                        ),
                    )
                else:
                    model_result = event.result

            if model_result is not None and self._model_observer is not None:
                try:
                    await self._model_observer(
                        context,
                        iteration,
                        model_result,
                    )
                except Exception:
                    pass

            if model_result is None or model_result.status == "failed":
                yield self._outcome(
                    "failed",
                    messages,
                    tool_call_count,
                    iterations=iteration,
                    error_code=(
                        model_result.error_code if model_result else "provider_failed"
                    ),
                )
                return
            if model_result.status == "cancelled" or context.cancellation.is_set():
                yield self._outcome(
                    "cancelled",
                    messages,
                    tool_call_count,
                    iterations=iteration,
                    error_code="cancelled",
                )
                return

            if not model_result.tool_calls:
                messages.append(
                    {
                        "role": "assistant",
                        "content": model_result.content,
                    }
                )
                yield self._outcome(
                    "completed",
                    messages,
                    tool_call_count,
                    iterations=iteration,
                    final_content=model_result.content,
                )
                return

            messages.append(
                self._assistant_tool_message(
                    model_result.content,
                    model_result.tool_calls,
                )
            )
            limit_reached = False
            for call in model_result.tool_calls:
                yield ReActEvent(
                    event_type="runtime_event",
                    runtime_event=RuntimeEvent(
                        event_type="tool_call",
                        payload=RuntimeEventPayload(
                            tool_call_id=call.call_id,
                            tool_name=call.name,
                            tool_args=call.arguments,
                            iteration=iteration,
                        ),
                    ),
                )
                if tool_call_count >= self._limits.max_tool_calls:
                    result = ToolStepResult(
                        call_id=call.call_id,
                        tool_name=call.name,
                        status="not_executed",
                        code="tool_limit_reached",
                        message="Tool call limit reached",
                    )
                    limit_reached = True
                else:
                    result = await self._tool_step.execute(
                        ToolStepContext(
                            scope=context.scope,
                            run_id=context.run_id,
                            client_request_id=context.client_request_id,
                            capabilities=context.capabilities,
                            cancellation=context.cancellation,
                            confirmation=context.confirmation,
                            commit=context.tool_commit,
                        ),
                        call,
                    )
                    tool_call_count += 1
                messages.append(self._tool_result_message(result))
                yield ReActEvent(
                    event_type="runtime_event",
                    runtime_event=RuntimeEvent(
                        event_type="tool_result",
                        payload=RuntimeEventPayload(
                            tool_call_id=result.call_id,
                            tool_name=result.tool_name,
                            tool_result=result.model_dump(mode="json"),
                            blocked=result.status != "completed",
                            iteration=iteration,
                        ),
                    ),
                )
                artifact = self._artifact_from(result.output)
                if artifact is not None:
                    yield ReActEvent(
                        event_type="runtime_event",
                        runtime_event=RuntimeEvent(
                            event_type="artifact",
                            payload=RuntimeEventPayload(
                                artifact=artifact,
                                iteration=iteration,
                            ),
                        ),
                    )

            if limit_reached or tool_call_count >= self._limits.max_tool_calls:
                yield self._outcome(
                    "failed",
                    messages,
                    tool_call_count,
                    iterations=iteration,
                    error_code="tool_limit_reached",
                )
                return

        yield self._outcome(
            "failed",
            messages,
            tool_call_count,
            iterations=self._limits.max_iterations,
            error_code="iteration_limit_reached",
        )

    @staticmethod
    def _assistant_tool_message(
        content: str,
        calls: tuple[ProposedToolCall, ...],
    ) -> dict[str, Any]:
        return {
            "role": "assistant",
            "content": content,
            "tool_calls": [
                {
                    "id": call.call_id,
                    "type": "function",
                    "function": {
                        "name": call.name,
                        "arguments": json.dumps(
                            call.arguments,
                            ensure_ascii=False,
                            sort_keys=True,
                        ),
                    },
                }
                for call in calls
            ],
        }

    @staticmethod
    def _tool_result_message(result: ToolStepResult) -> dict[str, Any]:
        serialized = json.dumps(
            result.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
        )
        message: dict[str, Any] = {
            "role": "tool",
            "tool_call_id": result.call_id,
            "content": serialized,
        }
        if isinstance(result.output, dict):
            image_ref = result.output.get("image_ref")
            if isinstance(image_ref, dict) and image_ref.get("type") == "image_ref":
                message["content"] = [
                    {"type": "text", "text": serialized},
                    dict(image_ref),
                ]
        return message

    @staticmethod
    def _artifact_from(output: Any) -> ArtifactRef | None:
        if not isinstance(output, dict) or not isinstance(output.get("artifact"), dict):
            return None
        try:
            return ArtifactRef.model_validate(output["artifact"])
        except ValueError:
            return None

    @staticmethod
    def _outcome(
        status: Literal["completed", "failed", "cancelled"],
        messages: list[dict[str, Any]],
        tool_calls: int,
        *,
        iterations: int = 0,
        final_content: str = "",
        error_code: str = "",
    ) -> ReActEvent:
        return ReActEvent(
            event_type="outcome",
            outcome=ReActOutcome(
                status=status,
                messages=tuple(messages),
                final_content=final_content,
                iterations=iterations,
                tool_calls=tool_calls,
                error_code=error_code,
            ),
        )
