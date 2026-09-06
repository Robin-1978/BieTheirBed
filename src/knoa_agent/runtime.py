"""Knoa Agent implementation of the neutral Agent Runtime SPI."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field

from knoa_agent.context import ContextBudgetExceeded, ContextEngine
from knoa_agent.context_store import ContextCheckpoint, ContextCheckpointRepository
from knoa_agent.tool_inventory import ToolInventory
from knoa_agent_contracts import (
    AgentDescriptor,
    AgentRuntime,
    ArtifactPart,
    AssistantDelta,
    ContextCompacted,
    CreateRuntimeSession,
    McpEndpointGrant,
    ReasoningSummaryDelta,
    ReconcileRuntime,
    ResourceLinkPart,
    ResumeRuntimeSession,
    RuntimeCommandResult,
    RuntimeHealth,
    RuntimeInteractionResolution,
    RuntimeInterruptCommand,
    RuntimeLimits,
    RuntimeObservedState,
    RuntimeSession,
    RuntimeSteerCommand,
    RuntimeTurn,
    RuntimeTurnEvent,
    RuntimeTurnRequest,
    TextPart,
    ToolCallFinished,
    ToolCallStarted,
    TurnFinished,
    UsageReported,
)

logger = logging.getLogger(__name__)

GUI_TOOLS = frozenset({"mouse", "ui", "press_key", "type_text", "hotkey"})


class AgentModelRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    call_id: str
    purpose: str = "react"
    messages: tuple[dict[str, Any], ...]
    tools: tuple[dict[str, Any], ...] = ()
    temperature: float = 0.2
    max_output_tokens: int = 1024


class ModelProvider(Protocol):
    def stream(
        self,
        request: AgentModelRequest,
        cancellation: asyncio.Event,
    ) -> AsyncIterator[Any]: ...


class McpToolCall(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    call_id: str
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class McpClient(Protocol):
    async def list_tools(self) -> tuple[dict[str, Any], ...]: ...

    async def call_tool(self, call: McpToolCall) -> Any: ...

    async def read_resource(self, uri: str) -> tuple[dict[str, Any], ...]: ...


class McpConnector(Protocol):
    def connect(
        self,
        grant: McpEndpointGrant,
    ) -> AbstractAsyncContextManager[McpClient]: ...


class ImageInputUnavailable(RuntimeError):
    """The main model cannot see images and no governed vision tool is available."""


class KnoaAgentRuntime(AgentRuntime):
    """Own model context and ReAct state; consume Platform abilities only by MCP."""

    _STATE_VERSION = "1"

    def __init__(
        self,
        provider: ModelProvider,
        context_store: ContextCheckpointRepository,
        mcp_connector: McpConnector,
        *,
        system_prompt: str,
        health_probe: Callable[[], Awaitable[Any]],
        max_iterations: int = 8,
        max_tool_calls: int = 50,
        max_output_tokens: int = 1024,
        temperature: float = 0.2,
        context_window: int = 8192,
        clock: Callable[[], float] = time.time,
        turn_id_factory: Callable[[], str] | None = None,
        tool_inventory: ToolInventory | None = None,
        agent_id: str = "knoa",
        display_name: str = "Knoa",
        supports_vision: bool = False,
        screen_verify_enabled: bool = False,
    ) -> None:
        if max_iterations <= 0 or max_tool_calls <= 0:
            raise ValueError("Knoa Agent limits must be positive")
        self._provider = provider
        self._contexts = context_store
        self._mcp = mcp_connector
        self._system_prompt = system_prompt
        self._health_probe = health_probe
        self._max_iterations = max_iterations
        self._max_tool_calls = max_tool_calls
        self._max_output_tokens = max_output_tokens
        self._temperature = temperature
        self._context = ContextEngine(
            context_window=context_window,
            completion_reserve=max_output_tokens,
        )
        self._clock = clock
        self._turn_id_factory = turn_id_factory or (lambda: uuid.uuid4().hex)
        self._tool_inventory = tool_inventory or ToolInventory()
        self._agent_id = agent_id
        self._display_name = display_name
        self._supports_vision = supports_vision
        self._screen_verify_enabled = screen_verify_enabled
        self._active: dict[str, tuple[RuntimeSession, asyncio.Event]] = {}
        self._operations: dict[str, str] = {}
        self._guard = asyncio.Lock()
        self._draining = False

    @property
    def descriptor(self) -> AgentDescriptor:
        return AgentDescriptor(
            agent_id=self._agent_id,
            display_name=self._display_name,
            implementation_version="1.0.0",
            capabilities=frozenset(
                {
                    "mcp.client",
                    "input.image",
                    "input.file",
                    "event.reasoning_summary",
                    "event.plan",
                    "event.tool_lifecycle",
                    "event.usage",
                    "event.context_compaction",
                }
            ),
            limits=RuntimeLimits(max_concurrent_turns=4),
        )

    async def create_session(self, request: CreateRuntimeSession) -> RuntimeSession:
        session = await asyncio.to_thread(
            self._contexts.create_session,
            operation_id=request.operation_id,
            state_version=self._STATE_VERSION,
        )
        return RuntimeSession(
            agent_id=self._agent_id,
            runtime_session_ref=session.runtime_session_ref,
            runtime_protocol_version="1.0",
            binding_epoch=request.binding_epoch,
        )

    async def resume_session(self, request: ResumeRuntimeSession) -> RuntimeSession:
        if request.session.agent_id != self._agent_id:
            raise ValueError("Runtime Session belongs to another Agent")
        session = await asyncio.to_thread(
            self._contexts.get_session,
            request.session.runtime_session_ref,
        )
        if session.state_version != self._STATE_VERSION:
            raise RuntimeError("session_not_resumable")
        return request.session

    async def start_turn(self, request: RuntimeTurnRequest) -> RuntimeTurn:
        if self._draining:
            raise RuntimeError("agent_unavailable")
        await self.resume_session(
            ResumeRuntimeSession(
                operation_id=request.operation_id,
                session=request.session,
            )
        )
        async with self._guard:
            existing = self._operations.get(request.operation_id)
            if existing is not None:
                raise RuntimeError("turn_outcome_requires_reconciliation")
            runtime_turn_ref = self._turn_id_factory()
            cancellation = asyncio.Event()
            self._operations[request.operation_id] = runtime_turn_ref
            self._active[runtime_turn_ref] = (request.session, cancellation)
        return RuntimeTurn(
            runtime_turn_ref=runtime_turn_ref,
            events=self._run_turn(request, runtime_turn_ref, cancellation),
        )

    async def _run_turn(
        self,
        request: RuntimeTurnRequest,
        runtime_turn_ref: str,
        cancellation: asyncio.Event,
    ) -> AsyncIterator[RuntimeTurnEvent]:
        terminal_emitted = False
        try:
            checkpoint = await asyncio.to_thread(
                self._contexts.load_checkpoint,
                request.session.runtime_session_ref,
            )
            durable_history = self._checkpoint_messages(checkpoint)
            summary, covered_messages = self._checkpoint_summary(checkpoint)
            async with self._mcp.connect(request.mcp) as client:
                inventory = await self._tool_inventory.load(
                    request.session.runtime_session_ref,
                    request.mcp.scope_digest,
                    client,
                )
                projection = await self._tool_inventory.project_for_turn(
                    request.session.runtime_session_ref,
                    inventory,
                    self._turn_query(request),
                )
                tools = projection.tools
                available_tool_names = {
                    str(tool.get("name") or "") for tool in tools if tool.get("name")
                }
                durable_user = self._durable_user_message(request)
                image_inspection_available = any(
                    str(tool.get("name") or "") == "image_inspect" for tool in tools
                )
                try:
                    model_user = await self._model_user_message(
                        request,
                        client,
                        image_inspection_available=image_inspection_available,
                    )
                except ImageInputUnavailable:
                    yield TurnFinished(
                        **self._event_base(request, runtime_turn_ref),
                        status="failed",
                        error_code="vision_unavailable",
                    )
                    terminal_emitted = True
                    return
                model_messages = [*durable_history, model_user]
                durable_messages = [*durable_history, durable_user]
                tool_calls = 0
                vision_required = any(
                    isinstance(part, ArtifactPart)
                    and (
                        part.presentation == "image"
                        or part.artifact.media_type.startswith("image/")
                    )
                    for part in request.input
                ) and not self._supports_vision
                vision_observations = 0
                final_output = ""
                usage: dict[str, int | float | str] = {}
                saved_checkpoint = False
                last_meaningful_content = ""
                content = ""
                for _iteration in range(1, self._max_iterations + 1):
                    if cancellation.is_set():
                        break
                    try:
                        prepared = self._context.prepare(
                            system_prompt=self._system_prompt,
                            model_history=model_messages,
                            durable_history=durable_messages,
                            tools=tools,
                            context=request.context,
                            summary=summary,
                            covered_messages=covered_messages,
                        )
                    except ContextBudgetExceeded:
                        if not saved_checkpoint and durable_messages:
                            await self._save_aborted_turn_checkpoint(
                                request,
                                checkpoint,
                                durable_messages,
                                summary=summary,
                                covered_messages=covered_messages,
                                reason="context_budget_exceeded",
                                partial_content=last_meaningful_content,
                            )
                            saved_checkpoint = True
                        yield TurnFinished(
                            **self._event_base(request, runtime_turn_ref),
                            status="failed",
                            error_code="context_budget_exceeded",
                        )
                        terminal_emitted = True
                        return
                    model_messages = list(prepared.model_history)
                    durable_messages = list(prepared.durable_history)
                    summary = prepared.summary
                    covered_messages = prepared.covered_messages
                    if prepared.compacted:
                        yield ContextCompacted(
                            **self._event_base(request, runtime_turn_ref),
                            source_cursor=covered_messages,
                            state_version=self._STATE_VERSION,
                            tokens_before=prepared.tokens_before,
                            tokens_after=prepared.tokens_after,
                        )
                    terminal_chunk = None
                    content_parts: list[str] = []
                    calls: tuple[Any, ...] = ()
                    model_request = AgentModelRequest(
                        call_id=uuid.uuid4().hex,
                        messages=prepared.messages,
                        tools=tools,
                        temperature=self._temperature,
                        max_output_tokens=self._max_output_tokens,
                    )
                    async for chunk in self._provider.stream(
                        model_request,
                        cancellation,
                    ):
                        if cancellation.is_set():
                            break
                        content_delta = str(getattr(chunk, "content_delta", "") or "")
                        reasoning_delta = str(
                            getattr(chunk, "reasoning_delta", "") or ""
                        )
                        if content_delta:
                            content_parts.append(content_delta)
                            yield AssistantDelta(
                                **self._event_base(request, runtime_turn_ref),
                                content=content_delta,
                            )
                        if reasoning_delta:
                            yield ReasoningSummaryDelta(
                                **self._event_base(request, runtime_turn_ref),
                                content=reasoning_delta,
                            )
                        if getattr(chunk, "terminal", False):
                            terminal_chunk = chunk
                            calls = tuple(getattr(chunk, "tool_calls", ()) or ())
                            raw_usage = getattr(chunk, "usage", {}) or {}
                            usage = {
                                str(key): value
                                for key, value in raw_usage.items()
                                if isinstance(value, (int, float, str))
                            }
                    if cancellation.is_set():
                        break
                    if content_parts:
                        content = "".join(content_parts)
                        if content.strip():
                            last_meaningful_content = content.strip()
                    if terminal_chunk is None or str(
                        getattr(terminal_chunk, "finish_reason", "")
                    ) == "error":
                        error_code = str(
                            getattr(terminal_chunk, "error_code", "provider_failed")
                            if terminal_chunk is not None
                            else "provider_failed"
                        )
                        if not saved_checkpoint and durable_messages:
                            await self._save_aborted_turn_checkpoint(
                                request,
                                checkpoint,
                                durable_messages,
                                summary=summary,
                                covered_messages=covered_messages,
                                reason=error_code or "provider_failed",
                                partial_content=last_meaningful_content,
                            )
                            saved_checkpoint = True
                        yield TurnFinished(
                            **self._event_base(request, runtime_turn_ref),
                            status="failed",
                            error_code=error_code or "provider_failed",
                        )
                        terminal_emitted = True
                        return
                    content = "".join(content_parts)
                    if content.strip():
                        last_meaningful_content = content.strip()
                    yield UsageReported(
                        **self._event_base(request, runtime_turn_ref),
                        usage={
                            **usage,
                            "cached_tokens": self._cached_tokens(raw_usage),
                            "iteration": _iteration,
                            "prompt_tokens_estimated": prepared.tokens_after,
                            "schema_tokens_estimated": prepared.schema_tokens,
                            "prompt_tokens_source": (
                                "provider"
                                if self._has_usage(
                                    usage, "prompt_tokens", "input_tokens"
                                )
                                else "estimated"
                            ),
                            "completion_tokens_source": (
                                "provider"
                                if self._has_usage(
                                    usage, "completion_tokens", "output_tokens"
                                )
                                else "unavailable"
                            ),
                            "available_tools": len(tools),
                            "inventory_tools": len(inventory.tools),
                            "selected_tools": len(tools),
                            "tool_selection_mode": projection.mode,
                            "tool_selection_hits": len(projection.matched_names),
                            "schema_hits": projection.schema_hits,
                            "provider_model": str(
                                getattr(terminal_chunk, "provider_model", "") or ""
                            ),
                            "finish_reason": str(
                                getattr(terminal_chunk, "finish_reason", "") or ""
                            ),
                            "tool_calls": len(calls),
                        },
                    )
                    if not calls:
                        if vision_required and vision_observations == 0:
                            model_messages.append({
                                "role": "user",
                                "content": (
                                    "[System] The response was not delivered because the attached image "
                                    "has not been observed. Call image_inspect now with the available "
                                    "artifact_id and a visual question derived from the user's request."
                                ),
                            })
                            durable_messages.append(model_messages[-1])
                            continue
                        final_output = content
                        model_messages.append({"role": "assistant", "content": content})
                        durable_messages.append(
                            {"role": "assistant", "content": content}
                        )
                        await self._save_checkpoint(
                            request,
                            checkpoint,
                            durable_messages,
                            summary=summary,
                            covered_messages=covered_messages,
                        )
                        saved_checkpoint = True
                        yield TurnFinished(
                            **self._event_base(request, runtime_turn_ref),
                            status="completed",
                            final_output=final_output,
                        )
                        terminal_emitted = True
                        return
                    assistant_message = self._assistant_tool_message(content, calls)
                    model_messages.append(assistant_message)
                    durable_messages.append(assistant_message)
                    for call in calls:
                        if tool_calls >= self._max_tool_calls:
                            if not saved_checkpoint and durable_messages:
                                await self._save_aborted_turn_checkpoint(
                                    request,
                                    checkpoint,
                                    durable_messages,
                                    summary=summary,
                                    covered_messages=covered_messages,
                                    reason="tool_limit_reached",
                                    partial_content=last_meaningful_content,
                                )
                                saved_checkpoint = True
                            yield TurnFinished(
                                **self._event_base(request, runtime_turn_ref),
                                status="failed",
                                error_code="tool_limit_reached",
                            )
                            terminal_emitted = True
                            return
                        proposed = McpToolCall(
                            call_id=str(call.call_id),
                            name=str(call.name),
                            arguments=dict(call.arguments),
                        )
                        yield ToolCallStarted(
                            **self._event_base(request, runtime_turn_ref),
                            tool_call_id=proposed.call_id,
                            tool_name=proposed.name,
                            arguments=proposed.arguments,
                        )
                        try:
                            result = await client.call_tool(proposed)
                        except Exception as exc:  # noqa: BLE001 - transport failures become tool results
                            tool_calls += 1
                            logger.exception(
                                "MCP tool call failed: %s (%s)",
                                proposed.name,
                                type(exc).__name__,
                            )
                            yield ToolCallFinished(
                                **self._event_base(request, runtime_turn_ref),
                                tool_call_id=proposed.call_id,
                                tool_name=proposed.name,
                                status="failed",
                                code="mcp_tool_call_failed",
                                output={
                                    "error": f"{type(exc).__name__}: {str(exc)[:1000]}"
                                },
                            )
                            result_message = {
                                "role": "tool",
                                "tool_call_id": proposed.call_id,
                                "content": json.dumps(
                                    {
                                        "call_id": proposed.call_id,
                                        "tool_name": proposed.name,
                                        "status": "failed",
                                        "code": "mcp_tool_call_failed",
                                        "message": f"{type(exc).__name__}: {str(exc)[:1000]}",
                                    },
                                    ensure_ascii=False,
                                    sort_keys=True,
                                    default=str,
                                ),
                            }
                            model_messages.append(result_message)
                            durable_messages.append(result_message)
                            continue
                        tool_calls += 1
                        yield ToolCallFinished(
                            **self._event_base(request, runtime_turn_ref),
                            tool_call_id=str(result.call_id),
                            tool_name=str(result.tool_name),
                            status=str(result.status),
                            code=str(result.code),
                            output=result.output,
                        )
                        result_message = {
                            "role": "tool",
                            "tool_call_id": str(result.call_id),
                            "content": json.dumps(
                                result.model_dump(mode="json"),
                                ensure_ascii=False,
                                sort_keys=True,
                                default=str,
                            ),
                        }
                        model_messages.append(result_message)
                        durable_messages.append(result_message)
                        verify_call = self._gui_verification_call(
                            tool_name=str(proposed.name),
                            tool_args=dict(proposed.arguments),
                            tool_result=result.output,
                            available_tool_names=available_tool_names,
                        )
                        if verify_call is not None and tool_calls < self._max_tool_calls:
                            yield ToolCallStarted(
                                **self._event_base(request, runtime_turn_ref),
                                tool_call_id=verify_call.call_id,
                                tool_name=verify_call.name,
                                arguments=verify_call.arguments,
                            )
                            try:
                                verify_result = await client.call_tool(verify_call)
                            except Exception as exc:
                                tool_calls += 1
                                logger.exception(
                                    "Automatic GUI verification failed for tool=%s",
                                    proposed.name,
                                )
                                yield ToolCallFinished(
                                    **self._event_base(request, runtime_turn_ref),
                                    tool_call_id=verify_call.call_id,
                                    tool_name=verify_call.name,
                                    status="failed",
                                    code="automatic_verification_failed",
                                    output={"error": str(exc)},
                                )
                            else:
                                tool_calls += 1
                                yield ToolCallFinished(
                                    **self._event_base(request, runtime_turn_ref),
                                    tool_call_id=str(verify_result.call_id),
                                    tool_name=str(verify_result.tool_name),
                                    status=str(verify_result.status),
                                    code=str(verify_result.code),
                                    output=verify_result.output,
                                )
                                verify_message = self._gui_verification_message(
                                    str(proposed.name),
                                    self._describe_gui_action(
                                        str(proposed.name), dict(proposed.arguments)
                                    ),
                                    verify_result,
                                )
                                if verify_message is not None:
                                    model_messages.append(verify_message)
                                    durable_messages.append(verify_message)
                        if (
                            proposed.name == "image_inspect"
                            and str(result.status) == "completed"
                            and isinstance(result.output, dict)
                            and result.output.get("observation_id")
                        ):
                            vision_observations += 1
                        if proposed.name == "tool_help":
                            activated = self._tool_inventory.activate(
                                request.session.runtime_session_ref,
                                inventory,
                                self._helped_tool_names(result.output),
                            )
                            if activated:
                                tools = self._tool_inventory.project(
                                    request.session.runtime_session_ref,
                                    inventory,
                                )
                                projection = projection.__class__(
                                    tools=tools,
                                    mode=projection.mode + "+tool_help",
                                    matched_names=tuple(
                                        sorted({*projection.matched_names, *activated})
                                    ),
                                    schema_hits=projection.schema_hits + len(activated),
                                )
                status = "interrupted" if cancellation.is_set() else "failed"
                error_code = ("cancelled" if cancellation.is_set() else "iteration_limit_reached")
                if not saved_checkpoint and durable_messages:
                    await self._save_aborted_turn_checkpoint(
                        request,
                        checkpoint,
                        durable_messages,
                        summary=summary,
                        covered_messages=covered_messages,
                        reason=error_code,
                        partial_content=last_meaningful_content,
                    )
                    saved_checkpoint = True
                yield TurnFinished(
                    **self._event_base(request, runtime_turn_ref),
                    status=status,
                    error_code=error_code,
                )
                terminal_emitted = True
        except asyncio.CancelledError:
            cancellation.set()
            if not saved_checkpoint and durable_messages:
                await self._save_aborted_turn_checkpoint(
                    request,
                    checkpoint,
                    durable_messages,
                    summary=summary,
                    covered_messages=covered_messages,
                    reason="cancelled",
                    partial_content=last_meaningful_content,
                )
                saved_checkpoint = True
            raise
        except Exception:
            logger.exception(
                "Knoa runtime turn failed runtime_turn_ref=%s",
                runtime_turn_ref,
            )
            if not saved_checkpoint and durable_messages:
                await self._save_aborted_turn_checkpoint(
                    request,
                    checkpoint,
                    durable_messages,
                    summary=summary,
                    covered_messages=covered_messages,
                    reason="runtime_failed",
                    partial_content=last_meaningful_content,
                )
                saved_checkpoint = True
            if not terminal_emitted:
                yield TurnFinished(
                    **self._event_base(request, runtime_turn_ref),
                    status="failed",
                    error_code="runtime_failed",
                )
        finally:
            async with self._guard:
                self._active.pop(runtime_turn_ref, None)

    async def interrupt_turn(
        self,
        command: RuntimeInterruptCommand,
    ) -> RuntimeCommandResult:
        async with self._guard:
            active = self._active.get(command.runtime_turn_ref)
            if active is None or active[0] != command.session:
                return RuntimeCommandResult(status="not_found")
            active[1].set()
        return RuntimeCommandResult(status="accepted")

    async def steer_turn(
        self,
        command: RuntimeSteerCommand,
    ) -> RuntimeCommandResult:
        del command
        return RuntimeCommandResult(status="rejected", code="capability_not_supported")

    async def resolve_interaction(
        self,
        command: RuntimeInteractionResolution,
    ) -> RuntimeCommandResult:
        del command
        return RuntimeCommandResult(status="rejected", code="capability_not_supported")

    async def reconcile(self, request: ReconcileRuntime) -> RuntimeObservedState:
        try:
            await asyncio.to_thread(
                self._contexts.get_session,
                request.session.runtime_session_ref,
            )
        except LookupError:
            return RuntimeObservedState(session_state="not_found")
        async with self._guard:
            active = request.runtime_turn_ref in self._active
        return RuntimeObservedState(
            session_state="ready",
            turn_state="running" if active else "none",
            runtime_turn_ref=request.runtime_turn_ref if active else "",
        )

    async def release_session(self, session: RuntimeSession) -> None:
        del session

    async def delete_session(self, session: RuntimeSession) -> None:
        async with self._guard:
            if any(active[0] == session for active in self._active.values()):
                raise RuntimeError("turn_not_active")
        await asyncio.to_thread(
            self._contexts.delete_session,
            session.runtime_session_ref,
        )
        self._tool_inventory.invalidate_session(session.runtime_session_ref)

    @staticmethod
    def _recent_tools(checkpoint: ContextCheckpoint | None) -> frozenset[str]:
        if checkpoint is None:
            return frozenset()
        messages = checkpoint.payload.get("messages", [])
        if not isinstance(messages, list):
            return frozenset()
        names = []
        for message in messages[-12:]:
            if not isinstance(message, dict):
                continue
            for call in message.get("tool_calls", ()):
                if not isinstance(call, dict):
                    continue
                function = call.get("function")
                if isinstance(function, dict) and function.get("name"):
                    names.append(str(function["name"]))
        return frozenset(names)

    @staticmethod
    def _turn_query(request: RuntimeTurnRequest) -> str:
        return "\n".join(
            part.text
            for part in request.input
            if isinstance(part, TextPart) and part.text
        )

    async def health_check(self) -> RuntimeHealth:
        try:
            health = await self._health_probe()
            healthy = bool(getattr(health, "healthy", False))
            detail = str(getattr(health, "detail", ""))
        except Exception:
            return RuntimeHealth(healthy=False, state="failed", detail="Model unavailable")
        return RuntimeHealth(
            healthy=healthy,
            state="ready" if healthy else "degraded",
            detail=detail,
        )

    async def drain(self, deadline: float) -> None:
        self._draining = True
        while self._clock() < deadline:
            async with self._guard:
                if not self._active:
                    return
            await asyncio.sleep(0.05)
        async with self._guard:
            cancellations = tuple(active[1] for active in self._active.values())
        for cancellation in cancellations:
            cancellation.set()

    def _event_base(
        self,
        request: RuntimeTurnRequest,
        runtime_turn_ref: str,
    ) -> dict[str, Any]:
        return {
            "runtime_session_ref": request.session.runtime_session_ref,
            "runtime_turn_ref": runtime_turn_ref,
            "occurred_at": self._clock(),
        }

    @staticmethod
    def _checkpoint_messages(
        checkpoint: ContextCheckpoint | None,
    ) -> list[dict[str, Any]]:
        if checkpoint is None:
            return []
        messages = checkpoint.payload.get("messages", [])
        if not isinstance(messages, list) or not all(
            isinstance(message, dict) for message in messages
        ):
            raise RuntimeError("Knoa Agent checkpoint is invalid")
        return [dict(message) for message in messages]

    @staticmethod
    def _durable_user_message(request: RuntimeTurnRequest) -> dict[str, Any]:
        lines = []
        for part in request.input:
            if isinstance(part, TextPart):
                lines.append(part.text)
            elif isinstance(part, ArtifactPart):
                lines.append(
                    f"[artifact: {part.artifact.name or part.artifact.artifact_id}; "
                    f"resource={part.resource_uri}; sha256={part.artifact.sha256}]"
                )
            elif isinstance(part, ResourceLinkPart):
                lines.append(f"[resource: {part.name or part.uri}; uri={part.uri}]")
        return {"role": "user", "content": "\n".join(lines)}

    @staticmethod
    def _input_text(request: RuntimeTurnRequest) -> str:
        return "\n".join(
            part.text for part in request.input if isinstance(part, TextPart)
        )

    async def _model_user_message(
        self,
        request: RuntimeTurnRequest,
        client: McpClient,
        *,
        image_inspection_available: bool,
    ) -> dict[str, Any]:
        blocks: list[dict[str, Any]] = []
        for part in request.input:
            if isinstance(part, TextPart):
                blocks.append({"type": "text", "text": part.text})
                continue
            uri = part.resource_uri if isinstance(part, ArtifactPart) else part.uri
            contents = await client.read_resource(uri)
            for content in contents:
                media_type = str(content.get("media_type") or "application/octet-stream")
                if "blob" in content and media_type.startswith("image/"):
                    if not self._supports_vision:
                        if not image_inspection_available:
                            raise ImageInputUnavailable("Dedicated vision tool is unavailable")
                        if not isinstance(part, ArtifactPart):
                            raise ImageInputUnavailable(
                                "Dedicated vision requires a governed Artifact reference"
                            )
                        artifact_id = part.artifact.artifact_id
                        blocks.append({
                            "type": "text",
                            "text": (
                                f"[Attached image artifact_id={artifact_id}; name="
                                f"{getattr(part, 'name', '') or getattr(getattr(part, 'artifact', None), 'name', '') or artifact_id}. "
                                "You cannot see its pixels. Call image_inspect before making any claim "
                                "about visible content.]"
                            ),
                        })
                        continue
                    encoded = str(content["blob"])
                    base64.b64decode(encoded, validate=True)
                    blocks.append(
                        {
                            "type": "image",
                            "image_url": f"data:{media_type};base64,{encoded}",
                            "media_type": media_type,
                        }
                    )
                    if isinstance(part, ArtifactPart):
                        artifact_id = part.artifact.artifact_id
                        name = (
                            getattr(part, "name", "")
                            or getattr(getattr(part, "artifact", None), "name", "")
                            or artifact_id
                        )
                        guidance = (
                            f"[Inline image: artifact_id={artifact_id}; name={name}. "
                            "Analyze it directly."
                        )
                        if image_inspection_available:
                            guidance += (
                                " Reserve image_inspect for follow-up reinspection."
                            )
                        blocks.append({
                            "type": "text",
                            "text": f"{guidance}]",
                        })
                elif "text" in content:
                    blocks.append({"type": "text", "text": str(content["text"])})
                else:
                    blocks.append(
                        {"type": "text", "text": f"[binary MCP Resource: {uri}]"}
                    )
        return {"role": "user", "content": blocks}

    async def _save_checkpoint(
        self,
        request: RuntimeTurnRequest,
        previous: ContextCheckpoint | None,
        messages: list[dict[str, Any]],
        *,
        summary: str,
        covered_messages: int,
    ) -> None:
        serialized = json.dumps(messages, ensure_ascii=False, sort_keys=True, default=str)
        checkpoint = ContextCheckpoint(
            runtime_session_ref=request.session.runtime_session_ref,
            state_version=self._STATE_VERSION,
            source_cursor=covered_messages + len(messages),
            agent_config_digest=hashlib.sha256(
                self._system_prompt.encode("utf-8")
            ).hexdigest(),
            model_context_digest=hashlib.sha256(serialized.encode("utf-8")).hexdigest(),
            payload={
                "messages": messages,
                "summary": summary,
                "covered_messages": covered_messages,
            },
            revision=previous.revision if previous is not None else 1,
            created_at=previous.created_at if previous is not None else 0.0,
            updated_at=0.0,
        )
        await asyncio.to_thread(
            self._contexts.save_checkpoint,
            checkpoint,
            expected_revision=(previous.revision if previous is not None else None),
        )

    @staticmethod
    def _sanitize_messages_for_abort(
        messages: list[dict[str, Any]],
        *,
        reason: str,
        partial_content: str = "",
    ) -> list[dict[str, Any]]:
        if not messages:
            return []
        sanitized: list[dict[str, Any]] = [dict(m) for m in messages]

        # 1. Fill missing tool responses for any tool calls in assistant messages
        for i, msg in enumerate(list(sanitized)):
            if msg.get("role") == "assistant" and msg.get("tool_calls"):
                tool_calls = msg["tool_calls"]
                call_ids = [
                    str(tc["id"])
                    for tc in tool_calls
                    if isinstance(tc, dict) and tc.get("id")
                ]
                seen_call_ids = set()
                search_idx = i + 1
                while search_idx < len(sanitized):
                    curr = sanitized[search_idx]
                    if curr.get("role") in ("user", "assistant"):
                        break
                    if curr.get("role") == "tool" and curr.get("tool_call_id"):
                        seen_call_ids.add(str(curr["tool_call_id"]))
                    search_idx += 1

                for call_id in call_ids:
                    if call_id not in seen_call_ids:
                        sanitized.insert(
                            search_idx,
                            {
                                "role": "tool",
                                "tool_call_id": call_id,
                                "content": json.dumps(
                                    {
                                        "status": "cancelled",
                                        "code": "turn_aborted",
                                        "message": f"Tool call was aborted ({reason})",
                                    },
                                    ensure_ascii=False,
                                ),
                            },
                        )
                        seen_call_ids.add(call_id)
                        search_idx += 1

        # 2. Ensure history ends with an assistant message
        last_msg = sanitized[-1]
        last_role = last_msg.get("role")

        if last_role == "tool":
            if partial_content.strip():
                note = (
                    "\n\n[Incomplete response due to timeout or cancellation]"
                    if reason == "cancelled"
                    else f"\n\n[Turn interrupted: {reason}]"
                )
                closing = partial_content.strip() + note
            else:
                closing = (
                    "[Incomplete response due to timeout or cancellation]"
                    if reason == "cancelled"
                    else f"[Turn interrupted: {reason}]"
                )
            sanitized.append({"role": "assistant", "content": closing})
        elif (
            last_role == "assistant"
            and last_msg.get("tool_calls")
            and not last_msg.get("content")
        ):
            if partial_content.strip():
                note = (
                    "\n\n[Incomplete response due to timeout or cancellation]"
                    if reason == "cancelled"
                    else f"\n\n[Turn interrupted: {reason}]"
                )
                closing = partial_content.strip() + note
            else:
                closing = (
                    "[Incomplete response due to timeout or cancellation]"
                    if reason == "cancelled"
                    else f"[Turn interrupted: {reason}]"
                )
            sanitized.append({"role": "assistant", "content": closing})
        elif last_role == "user":
            closing = (
                "[Incomplete response: turn timed out or cancelled]"
                if reason == "cancelled"
                else f"[Incomplete response: {reason}]"
            )
            sanitized.append({"role": "assistant", "content": closing})

        return sanitized

    async def _save_aborted_turn_checkpoint(
        self,
        request: RuntimeTurnRequest,
        checkpoint: ContextCheckpoint | None,
        messages: list[dict[str, Any]],
        *,
        summary: str,
        covered_messages: int,
        reason: str,
        partial_content: str = "",
    ) -> None:
        if not messages:
            return
        try:
            sanitized = self._sanitize_messages_for_abort(
                messages,
                reason=reason,
                partial_content=partial_content,
            )
            await self._save_checkpoint(
                request,
                checkpoint,
                sanitized,
                summary=summary,
                covered_messages=covered_messages,
            )
        except Exception:
            logger.exception(
                "Failed to save checkpoint for aborted turn in session=%s",
                request.session.runtime_session_ref,
            )

    @staticmethod
    def _checkpoint_summary(
        checkpoint: ContextCheckpoint | None,
    ) -> tuple[str, int]:
        if checkpoint is None:
            return "", 0
        summary = checkpoint.payload.get("summary", "")
        covered = checkpoint.payload.get("covered_messages", 0)
        if not isinstance(summary, str) or not isinstance(covered, int) or covered < 0:
            raise RuntimeError("Knoa Agent checkpoint summary is invalid")
        return summary, covered

    @staticmethod
    def _has_usage(usage: dict[str, Any], *names: str) -> bool:
        return any(isinstance(usage.get(name), (int, float)) for name in names)

    @staticmethod
    def _assistant_tool_message(content: str, calls: tuple[Any, ...]) -> dict[str, Any]:
        return {
            "role": "assistant",
            "content": content,
            "tool_calls": [
                {
                    "id": str(call.call_id),
                    "type": "function",
                    "function": {
                        "name": str(call.name),
                        "arguments": json.dumps(
                            dict(call.arguments),
                            ensure_ascii=False,
                            sort_keys=True,
                        ),
                    },
                }
                for call in calls
            ],
        }

    @staticmethod
    def _cached_tokens(usage: dict[str, Any]) -> int:
        for name in ("cached_tokens", "cache_read_input_tokens"):
            value = usage.get(name)
            if isinstance(value, (int, float)):
                return max(0, int(value))
        for details_name in ("prompt_tokens_details", "input_tokens_details"):
            details = usage.get(details_name)
            if not isinstance(details, dict):
                continue
            for name in ("cached_tokens", "cache_read_input_tokens"):
                value = details.get(name)
                if isinstance(value, (int, float)):
                    return max(0, int(value))
        return 0

    @staticmethod
    def _helped_tool_names(output: Any) -> frozenset[str]:
        """Extract exact tools resolved by the standard tool_help result."""

        if not isinstance(output, dict) or output.get("found") is not True:
            return frozenset()
        schema = output.get("schema")
        if not isinstance(schema, dict):
            return frozenset()
        name = str(schema.get("name") or output.get("tool") or "").strip()
        return frozenset({name}) if name else frozenset()

    def _gui_verification_call(
        self,
        *,
        tool_name: str,
        tool_args: dict[str, Any],
        tool_result: Any,
        available_tool_names: frozenset[str],
    ) -> McpToolCall | None:
        if not self._screen_verify_enabled:
            return None
        if tool_name not in GUI_TOOLS:
            return None
        if not self._should_verify_gui_action(tool_name, tool_args):
            return None
        if isinstance(tool_result, dict) and tool_result.get("error"):
            return None
        if "screen" not in available_tool_names:
            return None

        description = self._describe_gui_action(tool_name, tool_args)
        return McpToolCall(
            call_id=uuid.uuid4().hex,
            name="screen",
            arguments={
                "action": "verify",
                "action_description": description,
            },
        )

    @staticmethod
    def _gui_verification_message(
        tool_name: str,
        description: str,
        verify_result: Any,
    ) -> dict[str, Any] | None:
        if str(getattr(verify_result, "status", "")) != "completed":
            return None
        output = getattr(verify_result, "output", None)
        if not isinstance(output, dict):
            return None
        if output.get("error"):
            return {
                "role": "user",
                "content": (
                    f"[System] Automatic screen verification could not run after "
                    f"{tool_name}: {output['error']}. The action may not have succeeded."
                ),
            }
        if output.get("verified") is True:
            return None
        observation = str(output.get("observation") or "").strip()
        uncertain = bool(output.get("uncertain"))
        detail = observation or "The verifier could not confirm success."
        qualifier = "may not have" if uncertain else "did not appear to"
        return {
            "role": "user",
            "content": (
                f"[System] Automatic screen verification suggests the recent GUI action "
                f"({description}) {qualifier} succeed. Visual observation: {detail} "
                "Retry the action, choose another approach, or inspect the screen before continuing."
            ),
        }

    @staticmethod
    def _should_verify_gui_action(tool_name: str, tool_args: dict[str, Any]) -> bool:
        action = str(tool_args.get("action") or "").strip().casefold()
        if tool_name == "mouse":
            return action in {"click", "double_click", "right_click", "drag"}
        if tool_name == "ui":
            return action in {"click", "fill", "select", "focus"}
        return True

    @staticmethod
    def _describe_gui_action(tool_name: str, tool_args: dict[str, Any]) -> str:
        action = str(tool_args.get("action") or tool_name)
        if tool_name == "mouse":
            parts = [f"mouse {action}"]
            if tool_args.get("x") is not None and tool_args.get("y") is not None:
                parts.append(f"at ({tool_args['x']}, {tool_args['y']})")
            if tool_args.get("button"):
                parts.append(f"button={tool_args['button']}")
            return " ".join(parts)
        if tool_name == "ui":
            target = tool_args.get("element_path") or tool_args.get("name") or "element"
            parts = [f"ui {action}", f"target={target}"]
            if action in {"fill", "select"} and tool_args.get("value") is not None:
                parts.append(f"characters={len(str(tool_args['value']))}")
            return " ".join(parts)
        if tool_name == "press_key":
            return f"press key {tool_args.get('key', '')}".strip()
        if tool_name == "type_text":
            text = str(tool_args.get("text") or "")
            return f"type text characters={len(text)}"
        if tool_name == "hotkey":
            keys = tool_args.get("keys") or tool_args.get("hotkey") or tool_args.get("key")
            return f"hotkey {keys}"
        return f"{tool_name} {json.dumps(tool_args, ensure_ascii=False, sort_keys=True)}"
