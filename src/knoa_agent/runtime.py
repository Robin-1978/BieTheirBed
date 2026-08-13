"""Knoa Agent implementation of the neutral Agent Runtime SPI."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field

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
    PlanChanged,
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
    RuntimeWarning,
    TextPart,
    ToolCallFinished,
    ToolCallStarted,
    TurnFinished,
    UsageReported,
)


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
        history_char_budget: int = 120_000,
        clock: Callable[[], float] = time.time,
        turn_id_factory: Callable[[], str] | None = None,
        prompt_context: Callable[
            [str, frozenset[str]], Awaitable[str]
        ]
        | None = None,
        tool_inventory: ToolInventory | None = None,
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
        self._history_char_budget = history_char_budget
        self._clock = clock
        self._turn_id_factory = turn_id_factory or (lambda: uuid.uuid4().hex)
        self._prompt_context = prompt_context
        self._tool_inventory = tool_inventory or ToolInventory()
        self._active: dict[str, tuple[RuntimeSession, asyncio.Event]] = {}
        self._operations: dict[str, str] = {}
        self._guard = asyncio.Lock()
        self._draining = False

    @property
    def descriptor(self) -> AgentDescriptor:
        return AgentDescriptor(
            agent_id="knoa",
            display_name="Knoa",
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
            agent_id="knoa",
            runtime_session_ref=session.runtime_session_ref,
            runtime_protocol_version="1.0",
            binding_epoch=request.binding_epoch,
        )

    async def resume_session(self, request: ResumeRuntimeSession) -> RuntimeSession:
        if request.session.agent_id != "knoa":
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
            async with self._mcp.connect(request.mcp) as client:
                inventory = await self._tool_inventory.load(
                    request.session.runtime_session_ref,
                    request.mcp.scope_digest,
                    client,
                )
                prompt_context = (
                    await self._prompt_context(
                        self._input_text(request),
                        frozenset(str(tool["name"]) for tool in inventory.tools),
                    )
                    if self._prompt_context is not None
                    else ""
                )
                tools = self._tool_inventory.project(
                    request.session.runtime_session_ref,
                    inventory,
                )
                durable_user = self._durable_user_message(request)
                model_user = await self._model_user_message(request, client)
                model_messages = [*durable_history, model_user]
                durable_messages = [*durable_history, durable_user]
                compacted = self._compact(model_messages, durable_messages)
                if compacted:
                    yield ContextCompacted(
                        **self._event_base(request, runtime_turn_ref),
                        source_cursor=compacted,
                        state_version=self._STATE_VERSION,
                        tokens_before=0,
                        tokens_after=0,
                    )
                tool_calls = 0
                final_output = ""
                usage: dict[str, int | float | str] = {}
                for _iteration in range(1, self._max_iterations + 1):
                    if cancellation.is_set():
                        break
                    terminal_chunk = None
                    content_parts: list[str] = []
                    calls: tuple[Any, ...] = ()
                    model_request = AgentModelRequest(
                        call_id=uuid.uuid4().hex,
                        messages=tuple(
                            [
                                {
                                    "role": "system",
                                    "content": self._system_prompt,
                                },
                                *(
                                    [{"role": "user", "content": prompt_context}]
                                    if prompt_context
                                    else []
                                ),
                                *model_messages,
                            ]
                        ),
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
                    if terminal_chunk is None or str(
                        getattr(terminal_chunk, "finish_reason", "")
                    ) == "error":
                        error_code = str(
                            getattr(terminal_chunk, "error_code", "provider_failed")
                            if terminal_chunk is not None
                            else "provider_failed"
                        )
                        yield TurnFinished(
                            **self._event_base(request, runtime_turn_ref),
                            status="failed",
                            error_code=error_code or "provider_failed",
                        )
                        terminal_emitted = True
                        return
                    content = "".join(content_parts)
                    yield UsageReported(
                        **self._event_base(request, runtime_turn_ref),
                        usage={
                            **usage,
                            "cached_tokens": self._cached_tokens(raw_usage),
                            "iteration": _iteration,
                            "schema_tokens_estimated": max(
                                0,
                                len(
                                    json.dumps(
                                        tools,
                                        ensure_ascii=False,
                                        sort_keys=True,
                                    )
                                )
                                // 4,
                            ),
                            "available_tools": len(tools),
                            "inventory_tools": len(inventory.tools),
                            "selected_tools": len(tools),
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
                        final_output = content
                        model_messages.append({"role": "assistant", "content": content})
                        durable_messages.append(
                            {"role": "assistant", "content": content}
                        )
                        await self._save_checkpoint(
                            request,
                            checkpoint,
                            durable_messages,
                        )
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
                        result = await client.call_tool(proposed)
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
                status = "interrupted" if cancellation.is_set() else "failed"
                yield TurnFinished(
                    **self._event_base(request, runtime_turn_ref),
                    status=status,
                    error_code=("cancelled" if cancellation.is_set() else "iteration_limit_reached"),
                )
                terminal_emitted = True
        except asyncio.CancelledError:
            cancellation.set()
            raise
        except Exception:
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
                    encoded = str(content["blob"])
                    base64.b64decode(encoded, validate=True)
                    blocks.append(
                        {
                            "type": "image",
                            "image_url": f"data:{media_type};base64,{encoded}",
                            "media_type": media_type,
                        }
                    )
                elif "text" in content:
                    blocks.append({"type": "text", "text": str(content["text"])})
                else:
                    blocks.append(
                        {"type": "text", "text": f"[binary MCP Resource: {uri}]"}
                    )
        return {"role": "user", "content": blocks}

    def _compact(
        self,
        model_messages: list[dict[str, Any]],
        durable_messages: list[dict[str, Any]],
    ) -> int:
        removed = 0
        while len(json.dumps(model_messages, ensure_ascii=False, default=str)) > self._history_char_budget and len(model_messages) > 2:
            model_messages.pop(0)
            durable_messages.pop(0)
            removed += 1
        return removed

    async def _save_checkpoint(
        self,
        request: RuntimeTurnRequest,
        previous: ContextCheckpoint | None,
        messages: list[dict[str, Any]],
    ) -> None:
        serialized = json.dumps(messages, ensure_ascii=False, sort_keys=True, default=str)
        checkpoint = ContextCheckpoint(
            runtime_session_ref=request.session.runtime_session_ref,
            state_version=self._STATE_VERSION,
            source_cursor=len(messages),
            agent_config_digest=hashlib.sha256(
                self._system_prompt.encode("utf-8")
            ).hexdigest(),
            model_context_digest=hashlib.sha256(serialized.encode("utf-8")).hexdigest(),
            payload={"messages": messages},
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
