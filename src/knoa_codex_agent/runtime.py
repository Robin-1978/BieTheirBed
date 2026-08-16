"""Codex App Server implementation of the neutral Agent Runtime SPI."""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from knoa_agent_contracts import (
    AgentDescriptor,
    AgentRuntime,
    ArtifactPart,
    AssistantDelta,
    ContextCompacted,
    CreateRuntimeSession,
    InteractionRequested,
    PlanChanged,
    ReasoningSummaryDelta,
    ReconcileRuntime,
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
from knoa_codex_agent.app_server import CodexAppServerClient
from knoa_codex_agent.session_store import CodexSessionRepository


class AppServerClient(Protocol):
    async def start(self) -> None: ...
    async def request(self, method: str, params: dict[str, Any]) -> dict[str, Any]: ...
    async def respond(
        self,
        request_id: int | str,
        *,
        result: dict[str, Any] | None = None,
        error: dict[str, Any] | None = None,
    ) -> None: ...
    def events(self): ...
    async def close(self) -> None: ...


ClientFactory = Callable[[Mapping[str, str]], AppServerClient]


@dataclass
class _ActiveTurn:
    session: RuntimeSession
    upstream_thread_ref: str
    client: AppServerClient
    interaction_epoch: int = 0
    interactions: dict[str, tuple[int | str, str, int]] | None = None

    def __post_init__(self) -> None:
        if self.interactions is None:
            self.interactions = {}


class CodexAgentRuntime(AgentRuntime):
    """Map Codex Thread/Turn/Item semantics to the Knoa Agent SPI."""

    def __init__(
        self,
        sessions: CodexSessionRepository,
        *,
        agent_id: str = "codex",
        display_name: str = "Codex",
        instructions: str,
        command: Sequence[str] = ("codex", "app-server"),
        home: str | Path | None = None,
        cwd: str | Path,
        model: str = "",
        approval_policy: str = "never",
        sandbox: str = "read-only",
        request_timeout_seconds: float = 120.0,
        max_line_bytes: int = 4 * 1024 * 1024,
        max_event_queue: int = 1024,
        clock: Callable[[], float] = time.time,
        client_factory: ClientFactory | None = None,
    ) -> None:
        if not agent_id.strip():
            raise ValueError("Codex Agent ID is required")
        if not display_name.strip():
            raise ValueError("Codex Agent display name is required")
        if not instructions.strip():
            raise ValueError("Codex Agent Profile instructions are required")
        if sandbox not in {"read-only", "workspace-write"}:
            raise ValueError("Codex sandbox must be read-only or workspace-write")
        self._sessions = sessions
        self._agent_id = agent_id.strip()
        self._display_name = display_name.strip()
        self._instructions = instructions.strip()
        self._command = tuple(command)
        self._home = None if home in (None, "") else Path(home).expanduser().resolve()
        if self._home is not None:
            self._home.mkdir(parents=True, exist_ok=True, mode=0o700)
            self._home.chmod(0o700)
        self._cwd = str(Path(cwd).expanduser().resolve())
        self._model = model.strip()
        self._approval_policy = approval_policy
        self._sandbox = sandbox
        self._request_timeout = request_timeout_seconds
        self._max_line_bytes = max_line_bytes
        self._max_event_queue = max_event_queue
        self._clock = clock
        self._client_factory = client_factory or self._new_stdio_client
        self._active: dict[str, _ActiveTurn] = {}
        self._guard = asyncio.Lock()
        self._draining = False

    @property
    def descriptor(self) -> AgentDescriptor:
        return AgentDescriptor(
            agent_id=self._agent_id,
            display_name=self._display_name,
            implementation_version="1.0.0",
            protocol_name="codex-app-server",
            protocol_version="2",
            capabilities=frozenset(
                {
                    "turn.steer",
                    "interaction.user_input",
                    "mcp.client",
                    "input.image",
                    "input.file",
                    "input.audio",
                    "event.reasoning_summary",
                    "event.plan",
                    "event.tool_lifecycle",
                    "event.usage",
                    "event.context_compaction",
                }
            ),
            limits=RuntimeLimits(max_concurrent_turns=1),
        )

    async def create_session(self, request: CreateRuntimeSession) -> RuntimeSession:
        existing = await asyncio.to_thread(
            self._sessions.find_by_operation, request.operation_id
        )
        if existing is not None:
            return self._runtime_session(existing.runtime_session_ref, existing.binding_epoch)
        record = await asyncio.to_thread(
            self._sessions.create,
            operation_id=request.operation_id,
            binding_epoch=request.binding_epoch,
        )
        return self._runtime_session(record.runtime_session_ref, record.binding_epoch)

    async def resume_session(self, request: ResumeRuntimeSession) -> RuntimeSession:
        self._require_session(request.session)
        await asyncio.to_thread(
            self._sessions.get, request.session.runtime_session_ref
        )
        return request.session

    async def start_turn(self, request: RuntimeTurnRequest) -> RuntimeTurn:
        if self._draining:
            raise RuntimeError("agent_unavailable")
        self._require_session(request.session)
        record = await asyncio.to_thread(
            self._sessions.get, request.session.runtime_session_ref
        )
        existing = await asyncio.to_thread(
            self._sessions.find_turn, request.operation_id
        )
        if existing is not None:
            raise RuntimeError("turn_outcome_requires_reconciliation")
        client = self._client_factory(
            {"KNOA_CAPABILITY_GRANT": request.mcp.authorization}
        )
        await client.start()
        try:
            thread_config = self._turn_config(request)
            if record.upstream_thread_ref is None:
                result = await client.request(
                    "thread/start",
                    {**self._thread_params(), "config": thread_config},
                )
                upstream_thread_ref = self._thread_id(result)
            else:
                upstream_thread_ref = record.upstream_thread_ref
                await client.request(
                    "thread/resume",
                    {
                        **self._thread_params(),
                        "threadId": upstream_thread_ref,
                        "config": thread_config,
                    },
                )
            await self._verify_mcp(client, upstream_thread_ref)
            inputs = await self._inputs(client, request, upstream_thread_ref)
            result = await client.request(
                "turn/start",
                {
                    "threadId": upstream_thread_ref,
                    "input": inputs,
                    "approvalPolicy": self._approval_policy,
                    "sandboxPolicy": self._sandbox_policy(request.options),
                    "collaborationMode": {
                        "mode": "default",
                        "settings": {
                            "model": self._model,
                            "developer_instructions": self._instructions,
                        },
                    },
                    **({"model": self._model} if self._model else {}),
                },
            )
            turn = result.get("turn")
            if not isinstance(turn, dict) or not str(turn.get("id") or "").strip():
                raise RuntimeError("Codex turn/start returned no Turn ID")
            turn_id = str(turn["id"])
            if record.upstream_thread_ref is None:
                await asyncio.to_thread(
                    self._sessions.bind_upstream_thread,
                    request.session.runtime_session_ref,
                    upstream_thread_ref,
                )
            recorded = await asyncio.to_thread(
                self._sessions.record_turn,
                operation_id=request.operation_id,
                runtime_session_ref=request.session.runtime_session_ref,
                runtime_turn_ref=turn_id,
            )
            if recorded != turn_id:
                raise RuntimeError("turn_outcome_requires_reconciliation")
            active = _ActiveTurn(request.session, upstream_thread_ref, client)
            async with self._guard:
                self._active[turn_id] = active
            return RuntimeTurn(
                runtime_turn_ref=turn_id,
                events=self._events(request, turn_id, active),
            )
        except BaseException:
            await client.close()
            raise

    async def _events(
        self,
        request: RuntimeTurnRequest,
        turn_id: str,
        active: _ActiveTurn,
    ):
        terminal = False
        final_parts: list[str] = []
        try:
            async for message in active.client.events():
                method = str(message.get("method") or "")
                params = message.get("params")
                if not isinstance(params, dict):
                    params = {}
                message_turn = self._message_turn_id(params)
                if message_turn and message_turn != turn_id:
                    continue
                base = self._event_base(request, turn_id, message)
                if method == "item/agentMessage/delta":
                    content = str(params.get("delta") or "")
                    final_parts.append(content)
                    yield AssistantDelta(**base, content=content)
                elif method == "item/reasoning/summaryTextDelta":
                    yield ReasoningSummaryDelta(
                        **base, content=str(params.get("delta") or "")
                    )
                elif method == "turn/plan/updated":
                    yield PlanChanged(**base, content=self._plan_text(params))
                elif method == "thread/tokenUsage/updated":
                    usage = params.get("tokenUsage")
                    yield UsageReported(
                        **base,
                        usage=self._flatten_usage(usage if isinstance(usage, dict) else {}),
                    )
                elif method in {"item/started", "item/completed"}:
                    event = self._item_event(method, params, base)
                    if event is not None:
                        yield event
                elif method in {"warning", "configWarning", "error"}:
                    yield RuntimeWarning(
                        **base,
                        code=method.replace("/", "_"),
                        message=self._warning_message(params),
                    )
                elif "id" in message and method:
                    if method in {
                        "item/commandExecution/requestApproval",
                        "item/fileChange/requestApproval",
                    }:
                        await active.client.respond(
                            message["id"],
                            result={"decision": "decline"},
                        )
                        yield RuntimeWarning(
                            **base,
                            code="native_approval_not_supported",
                            message=(
                                "Codex native approval was declined; use Knoa "
                                "Capability Gateway approval instead."
                            ),
                        )
                        continue
                    interaction = self._interaction_event(message, params, base, active)
                    if interaction is not None:
                        yield interaction
                    else:
                        await active.client.respond(
                            message["id"],
                            error={"code": -32601, "message": "Unsupported client request"},
                        )
                elif method == "turn/completed":
                    turn = params.get("turn")
                    if not isinstance(turn, dict) or str(turn.get("id") or "") != turn_id:
                        continue
                    status = str(turn.get("status") or "failed")
                    final_output = self._final_output(turn) or "".join(final_parts)
                    yield TurnFinished(
                        **base,
                        status=(
                            "completed"
                            if status == "completed"
                            else "interrupted"
                            if status == "interrupted"
                            else "failed"
                        ),
                        final_output=final_output,
                        error_code=self._turn_error_code(turn),
                    )
                    terminal = True
                    return
            if not terminal:
                yield TurnFinished(
                    **self._event_base(request, turn_id, {}),
                    status="outcome_unknown",
                    error_code="transport_closed_without_terminal",
                )
                terminal = True
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if not terminal:
                yield TurnFinished(
                    **self._event_base(request, turn_id, {}),
                    status="outcome_unknown",
                    error_code=type(exc).__name__,
                )
                terminal = True
        finally:
            async with self._guard:
                self._active.pop(turn_id, None)
            await active.client.close()

    async def steer_turn(self, command: RuntimeSteerCommand) -> RuntimeCommandResult:
        active = await self._active_turn(command.session, command.runtime_turn_ref)
        if active is None:
            return RuntimeCommandResult(status="not_found")
        try:
            result = await active.client.request(
                "turn/steer",
                {
                    "threadId": active.upstream_thread_ref,
                    "expectedTurnId": command.runtime_turn_ref,
                    "input": self._text_inputs(command.input),
                },
            )
        except Exception:
            return RuntimeCommandResult(status="unknown", code="transport_failed")
        accepted = str(result.get("turnId") or "") == command.runtime_turn_ref
        return RuntimeCommandResult(status="accepted" if accepted else "rejected")

    async def interrupt_turn(
        self, command: RuntimeInterruptCommand
    ) -> RuntimeCommandResult:
        active = await self._active_turn(command.session, command.runtime_turn_ref)
        if active is None:
            return RuntimeCommandResult(status="not_found")
        try:
            await active.client.request(
                "turn/interrupt",
                {
                    "threadId": active.upstream_thread_ref,
                    "turnId": command.runtime_turn_ref,
                },
            )
        except Exception:
            return RuntimeCommandResult(status="unknown", code="transport_failed")
        return RuntimeCommandResult(status="accepted")

    async def resolve_interaction(
        self, command: RuntimeInteractionResolution
    ) -> RuntimeCommandResult:
        active = await self._active_turn(command.session, command.runtime_turn_ref)
        if active is None:
            return RuntimeCommandResult(status="not_found")
        assert active.interactions is not None
        pending = active.interactions.get(command.interaction_id)
        if pending is None or pending[2] != command.interaction_epoch:
            return RuntimeCommandResult(status="not_found")
        request_id, method, _epoch = pending
        try:
            result = self._interaction_result(method, command.value)
            await active.client.respond(
                request_id, result=result
            )
        except ValueError as exc:
            return RuntimeCommandResult(status="rejected", code=str(exc))
        except Exception:
            return RuntimeCommandResult(status="unknown", code="transport_failed")
        active.interactions.pop(command.interaction_id, None)
        return RuntimeCommandResult(status="accepted")

    async def reconcile(self, request: ReconcileRuntime) -> RuntimeObservedState:
        try:
            await asyncio.to_thread(
                self._sessions.get, request.session.runtime_session_ref
            )
        except LookupError:
            return RuntimeObservedState(session_state="not_found")
        if request.runtime_turn_ref:
            async with self._guard:
                if request.runtime_turn_ref in self._active:
                    return RuntimeObservedState(
                        session_state="ready",
                        turn_state="running",
                        runtime_turn_ref=request.runtime_turn_ref,
                    )
        record = await asyncio.to_thread(
            self._sessions.get, request.session.runtime_session_ref
        )
        if record.upstream_thread_ref is None:
            return RuntimeObservedState(session_state="ready")
        client = self._client_factory({})
        await client.start()
        try:
            result = await client.request(
                "thread/read",
                {"threadId": record.upstream_thread_ref, "includeTurns": True},
            )
        except Exception:
            return RuntimeObservedState(
                session_state="ready",
                turn_state="unknown",
                runtime_turn_ref=request.runtime_turn_ref,
            )
        finally:
            await client.close()
        thread = result.get("thread")
        turns = thread.get("turns") if isinstance(thread, dict) else None
        if isinstance(turns, list):
            for turn in reversed(turns):
                if not isinstance(turn, dict):
                    continue
                if request.runtime_turn_ref and str(turn.get("id") or "") != request.runtime_turn_ref:
                    continue
                state = str(turn.get("status") or "")
                mapped = {
                    "inProgress": "running",
                    "completed": "completed",
                    "interrupted": "interrupted",
                    "failed": "failed",
                }.get(state, "unknown")
                return RuntimeObservedState(
                    session_state="ready",
                    turn_state=mapped,
                    runtime_turn_ref=str(turn.get("id") or ""),
                )
        return RuntimeObservedState(session_state="ready")

    async def release_session(self, session: RuntimeSession) -> None:
        self._require_session(session)
        record = await asyncio.to_thread(self._sessions.get, session.runtime_session_ref)
        if record.upstream_thread_ref is None:
            return
        client = self._client_factory({})
        await client.start()
        try:
            await client.request(
                "thread/unsubscribe", {"threadId": record.upstream_thread_ref}
            )
        finally:
            await client.close()

    async def delete_session(self, session: RuntimeSession) -> None:
        self._require_session(session)
        async with self._guard:
            if any(active.session == session for active in self._active.values()):
                raise RuntimeError("turn_active")
        record = await asyncio.to_thread(self._sessions.get, session.runtime_session_ref)
        if record.upstream_thread_ref is not None:
            client = self._client_factory({})
            await client.start()
            try:
                await client.request("thread/delete", {"threadId": record.upstream_thread_ref})
            finally:
                await client.close()
        await asyncio.to_thread(self._sessions.delete, session.runtime_session_ref)

    async def health_check(self) -> RuntimeHealth:
        if self._draining:
            return RuntimeHealth(healthy=True, state="draining")
        client = self._client_factory({})
        try:
            await client.start()
        except Exception as exc:
            return RuntimeHealth(
                healthy=False, state="failed", detail=f"{type(exc).__name__}: {exc}"
            )
        finally:
            await client.close()
        return RuntimeHealth(healthy=True, state="ready")

    async def drain(self, deadline: float) -> None:
        self._draining = True
        while self._clock() < deadline:
            async with self._guard:
                if not self._active:
                    return
            await asyncio.sleep(0.05)
        async with self._guard:
            active_turns = tuple(self._active.items())
        await asyncio.gather(
            *(
                self.interrupt_turn(
                    RuntimeInterruptCommand(
                        session=active.session,
                        runtime_turn_ref=turn_id,
                        command_id=f"drain:{turn_id}:{time.time_ns()}",
                        reason="Agent generation drain deadline exceeded",
                    )
                )
                for turn_id, active in active_turns
            ),
            return_exceptions=True,
        )

    def _new_stdio_client(self, extra_env: Mapping[str, str]) -> AppServerClient:
        environment = dict(extra_env)
        if self._home is not None:
            environment["CODEX_HOME"] = str(self._home)
        return CodexAppServerClient(
            self._command,
            cwd=self._cwd,
            env=environment,
            request_timeout_seconds=self._request_timeout,
            max_line_bytes=self._max_line_bytes,
            max_event_queue=self._max_event_queue,
        )

    def _thread_params(self) -> dict[str, Any]:
        return {
            "cwd": self._cwd,
            "approvalPolicy": self._approval_policy,
            "sandbox": self._sandbox,
            "serviceName": "knoa-platform",
            **({"model": self._model} if self._model else {}),
        }

    @staticmethod
    def _thread_id(result: dict[str, Any]) -> str:
        thread = result.get("thread")
        thread_id = str(thread.get("id") or "") if isinstance(thread, dict) else ""
        if not thread_id:
            raise RuntimeError("Codex thread/start returned no Thread ID")
        return thread_id

    def _turn_config(self, request: RuntimeTurnRequest) -> dict[str, Any]:
        if request.mcp.transport != "streamable_http":
            raise ValueError("Codex Agent requires a streamable HTTP MCP endpoint")
        return {
            "mcp_servers": {
                "knoa_platform": {
                    "url": request.mcp.endpoint,
                    "bearer_token_env_var": "KNOA_CAPABILITY_GRANT",
                    "required": True,
                    "default_tools_approval_mode": "approve",
                }
            },
            "apps": {"_default": {"enabled": False}},
            "agents": {"enabled": False},
            "web_search": "disabled",
        }

    async def _verify_mcp(self, client: AppServerClient, thread_id: str) -> None:
        result = await client.request(
            "mcpServerStatus/list",
            {"threadId": thread_id, "detail": "toolsAndAuthOnly", "limit": 100},
        )
        rows = result.get("data")
        if not isinstance(rows, list):
            raise RuntimeError("Codex returned an invalid MCP server inventory")
        names = {
            str(row.get("name") or row.get("serverName") or "")
            for row in rows
            if isinstance(row, dict)
        }
        if names != {"knoa_platform"}:
            raise RuntimeError(
                "Codex MCP inventory must contain only the Knoa capability server"
            )

    async def _inputs(
        self,
        client: AppServerClient,
        request: RuntimeTurnRequest,
        upstream_thread_ref: str,
    ) -> list[dict[str, Any]]:
        inputs: list[dict[str, Any]] = []
        platform_context = self._platform_context_input(request)
        if platform_context is not None:
            inputs.append(platform_context)
        for part in request.input:
            if isinstance(part, TextPart):
                inputs.append({"type": "text", "text": part.text})
                continue
            uri = part.resource_uri if isinstance(part, ArtifactPart) else part.uri
            result = await client.request(
                "mcpServer/resource/read",
                {
                    "server": "knoa_platform",
                    "threadId": upstream_thread_ref,
                    "uri": uri,
                },
            )
            contents = result.get("contents")
            if not isinstance(contents, list):
                raise RuntimeError("Codex MCP Resource read returned invalid contents")
            for content in contents:
                if not isinstance(content, dict):
                    continue
                media_type = str(content.get("mimeType") or "application/octet-stream")
                if "text" in content:
                    inputs.append({"type": "text", "text": str(content["text"])})
                elif "blob" in content and media_type.startswith("image/"):
                    inputs.append(
                        {
                            "type": "image",
                            "url": f"data:{media_type};base64,{content['blob']}",
                        }
                    )
                elif "blob" in content and media_type.startswith("audio/"):
                    inputs.append(
                        {
                            "type": "audio",
                            "url": f"data:{media_type};base64,{content['blob']}",
                        }
                    )
                else:
                    inputs.append(
                        {
                            "type": "text",
                            "text": f"A binary MCP Resource is available at {uri} ({media_type}).",
                        }
                    )
        if not inputs:
            raise ValueError("Codex Turn requires at least one supported input")
        return inputs

    @staticmethod
    def _platform_context_input(
        request: RuntimeTurnRequest,
    ) -> dict[str, Any] | None:
        context = request.context
        if not (
            context.core_memory
            or context.relevant_memory
            or context.episodic_memory
            or context.skill_instructions
        ):
            return None
        payload = {
            "provenance": "knoa_platform",
            "semantic_role": "context_not_user_command",
            "core_memory": context.core_memory,
            "relevant_memory": context.relevant_memory,
            "episodic_memory": context.episodic_memory,
            "skill_instructions": context.skill_instructions,
        }
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        return {
            "type": "text",
            "text": f"<knoa_runtime_context>{encoded}</knoa_runtime_context>",
        }

    @staticmethod
    def _text_inputs(parts) -> list[dict[str, Any]]:
        result = [
            {"type": "text", "text": part.text}
            for part in parts
            if isinstance(part, TextPart)
        ]
        if len(result) != len(parts):
            raise ValueError("Codex steering currently accepts text input only")
        return result

    def _sandbox_policy(
        self,
        options: dict[str, bool | int | float | str] | None = None,
    ) -> dict[str, Any]:
        requested = str((options or {}).get("native_capabilities") or "")
        capabilities = frozenset(
            item for item in requested.split(",") if item
        )
        if capabilities:
            read_only = frozenset({"workspace_read", "command_execution"})
            workspace_write = frozenset(
                {
                    "workspace_read",
                    "workspace_write",
                    "command_execution",
                    "native_file_edit",
                }
            )
            if capabilities == read_only:
                return {"type": "readOnly"}
            if self._sandbox == "workspace-write" and capabilities == workspace_write:
                return {
                    "type": "workspaceWrite",
                    "writableRoots": [self._cwd],
                    "networkAccess": False,
                }
            raise RuntimeError(
                "Codex Runtime cannot enforce the resolved native capability set"
            )
        if "native_capabilities" in (options or {}):
            raise RuntimeError("Codex Runtime requires explicit native capabilities")
        if self._sandbox == "read-only":
            return {"type": "readOnly"}
        return {
            "type": "workspaceWrite",
            "writableRoots": [self._cwd],
            "networkAccess": False,
        }

    def _runtime_session(self, ref: str, epoch: int) -> RuntimeSession:
        return RuntimeSession(
            agent_id=self._agent_id,
            runtime_session_ref=ref,
            runtime_protocol_version="2",
            binding_epoch=epoch,
        )

    def _require_session(self, session: RuntimeSession) -> None:
        if (
            session.agent_id != self._agent_id
            or session.runtime_protocol_version != "2"
        ):
            raise ValueError("Runtime Session belongs to another Agent")

    async def _active_turn(
        self, session: RuntimeSession, turn_id: str
    ) -> _ActiveTurn | None:
        self._require_session(session)
        async with self._guard:
            active = self._active.get(turn_id)
        return active if active is not None and active.session == session else None

    def _event_base(
        self, request: RuntimeTurnRequest, turn_id: str, message: dict[str, Any]
    ) -> dict[str, Any]:
        return {
            "source_event_id": str(message.get("eventId") or "") or None,
            "runtime_session_ref": request.session.runtime_session_ref,
            "runtime_turn_ref": turn_id,
            "occurred_at": self._clock(),
        }

    @staticmethod
    def _message_turn_id(params: dict[str, Any]) -> str:
        if params.get("turnId"):
            return str(params["turnId"])
        turn = params.get("turn")
        return str(turn.get("id") or "") if isinstance(turn, dict) else ""

    @staticmethod
    def _plan_text(params: dict[str, Any]) -> str:
        plan = params.get("plan")
        if not isinstance(plan, list):
            return ""
        return json.dumps(
            {
                "explanation": params.get("explanation"),
                "plan": plan,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )

    @staticmethod
    def _flatten_usage(value: dict[str, Any]) -> dict[str, int | float | str]:
        result: dict[str, int | float | str] = {}
        for prefix, section in value.items():
            if isinstance(section, dict):
                for key, item in section.items():
                    if isinstance(item, (int, float, str)):
                        result[f"{prefix}.{key}"] = item
            elif isinstance(section, (int, float, str)):
                result[str(prefix)] = section
        return result

    @staticmethod
    def _item_event(
        method: str, params: dict[str, Any], base: dict[str, Any]
    ) -> RuntimeTurnEvent | None:
        item = params.get("item")
        if not isinstance(item, dict):
            return None
        item_type = str(item.get("type") or "")
        item_id = str(item.get("id") or "")
        if item_type == "contextCompaction" and method == "item/completed":
            return ContextCompacted(
                **base,
                source_cursor=0,
                state_version=item_id or "codex",
                tokens_before=0,
                tokens_after=0,
            )
        if item_type not in {"mcpToolCall", "commandExecution", "fileChange"}:
            return None
        tool_name = (
            f"mcp__{item.get('server')}__{item.get('tool')}"
            if item_type == "mcpToolCall"
            else item_type
        )
        if method == "item/started":
            arguments = item.get("arguments")
            if item_type == "commandExecution":
                arguments = {"command": item.get("command"), "cwd": item.get("cwd")}
            elif item_type == "fileChange":
                arguments = {"changes": item.get("changes")}
            return ToolCallStarted(
                **base,
                tool_call_id=item_id,
                tool_name=str(tool_name),
                arguments=arguments if isinstance(arguments, dict) else {},
            )
        status = str(item.get("status") or "failed")
        mapped = {
            "completed": "completed",
            "failed": "failed",
            "declined": "rejected",
            "rejected": "rejected",
        }.get(status, "not_executed")
        return ToolCallFinished(
            **base,
            tool_call_id=item_id,
            tool_name=str(tool_name),
            status=mapped,
            code=status,
            output=item.get("result") or item.get("aggregatedOutput") or item.get("changes"),
        )

    def _interaction_event(
        self,
        message: dict[str, Any],
        params: dict[str, Any],
        base: dict[str, Any],
        active: _ActiveTurn,
    ) -> InteractionRequested | None:
        method = str(message.get("method") or "")
        kinds = {
            "item/tool/requestUserInput": "user_input",
            "mcpServer/elicitation/request": "mcp_elicitation",
        }
        kind = kinds.get(method)
        if kind is None:
            return None
        active.interaction_epoch += 1
        interaction_id = f"codex-{message['id']}"
        assert active.interactions is not None
        active.interactions[interaction_id] = (
            message["id"],
            method,
            active.interaction_epoch,
        )
        display = {"method": method, "params": params}
        resolution_schema: dict[str, Any] = {}
        if method == "item/tool/requestUserInput":
            display, resolution_schema = self._user_input_contract(params)
        return InteractionRequested(
            **base,
            interaction_id=interaction_id,
            interaction_epoch=active.interaction_epoch,
            kind=kind,
            display=display,
            resolution_schema=resolution_schema,
        )

    @staticmethod
    def _user_input_contract(
        params: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        raw_questions = params.get("questions")
        if not isinstance(raw_questions, list) or not 1 <= len(raw_questions) <= 3:
            raise ValueError("Codex user-input request must contain 1-3 questions")
        questions: list[dict[str, Any]] = []
        properties: dict[str, Any] = {}
        required: list[str] = []
        seen: set[str] = set()
        for raw in raw_questions:
            if not isinstance(raw, dict):
                raise ValueError("Codex user-input question is invalid")
            question_id = str(raw.get("id") or "").strip()
            header = str(raw.get("header") or "").strip()
            prompt = str(raw.get("question") or "").strip()
            if (
                not question_id
                or len(question_id) > 128
                or question_id in seen
                or not prompt
                or bool(raw.get("isSecret"))
            ):
                raise ValueError("Codex user-input question is unsupported")
            seen.add(question_id)
            options_raw = raw.get("options")
            options: list[dict[str, str]] = []
            labels: list[str] = []
            if options_raw is not None:
                if not isinstance(options_raw, list) or not 1 <= len(options_raw) <= 50:
                    raise ValueError("Codex user-input options are invalid")
                for option in options_raw:
                    if not isinstance(option, dict):
                        raise ValueError("Codex user-input option is invalid")
                    label = str(option.get("label") or "").strip()
                    description = str(option.get("description") or "").strip()
                    if not label or len(label) > 256 or label in labels:
                        raise ValueError("Codex user-input option is invalid")
                    labels.append(label)
                    options.append({"value": label, "label": label, "description": description})
            allow_other = bool(raw.get("isOther"))
            field_schema: dict[str, Any] = {
                "type": "string",
                "title": header or prompt[:80],
                "minLength": 1,
                "maxLength": 4000,
            }
            if labels and not allow_other:
                field_schema["enum"] = labels
            properties[question_id] = field_schema
            required.append(question_id)
            questions.append(
                {
                    "id": question_id,
                    "title": header,
                    "description": prompt,
                    "options": options,
                    "allow_other": allow_other,
                }
            )
        return (
            {
                "title": "Input required",
                "description": "The agent needs information to continue.",
                "fields": questions,
            },
            {
                "type": "object",
                "properties": properties,
                "required": required,
                "additionalProperties": False,
            },
        )

    @staticmethod
    def _interaction_result(method: str, value: Any) -> dict[str, Any]:
        if method in {
            "item/commandExecution/requestApproval",
            "item/fileChange/requestApproval",
        }:
            decision = value.get("decision") if isinstance(value, dict) else value
            normalized = str(decision or "decline")
            if normalized not in {"accept", "decline", "cancel"}:
                raise ValueError("Codex approval only permits a one-shot decision")
            return {"decision": normalized}
        if method == "item/tool/requestUserInput":
            if not isinstance(value, dict):
                raise ValueError("Codex user-input resolution is invalid")
            answers: dict[str, dict[str, list[str]]] = {}
            for question_id, answer in value.items():
                if not isinstance(question_id, str) or not question_id:
                    raise ValueError("Codex user-input resolution is invalid")
                if isinstance(answer, str):
                    normalized = [answer]
                elif (
                    isinstance(answer, list)
                    and answer
                    and all(isinstance(item, str) for item in answer)
                ):
                    normalized = list(answer)
                else:
                    raise ValueError("Codex user-input resolution is invalid")
                answers[question_id] = {"answers": normalized}
            return {"answers": answers}
        if method == "mcpServer/elicitation/request":
            if not isinstance(value, dict):
                raise ValueError("Codex MCP elicitation resolution is invalid")
            action = str(value.get("action") or "decline")
            if action not in {"accept", "decline", "cancel"}:
                raise ValueError("Codex MCP elicitation action is invalid")
            return {
                "action": action,
                "content": value.get("content") if action == "accept" else None,
            }
        return {}

    @staticmethod
    def _warning_message(params: dict[str, Any]) -> str:
        error = params.get("error")
        if isinstance(error, dict):
            return str(error.get("message") or error)
        return str(params.get("message") or params.get("summary") or params)

    @staticmethod
    def _final_output(turn: dict[str, Any]) -> str:
        items = turn.get("items")
        if not isinstance(items, list):
            return ""
        for item in reversed(items):
            if isinstance(item, dict) and item.get("type") == "agentMessage":
                return str(item.get("text") or "")
        return ""

    @staticmethod
    def _turn_error_code(turn: dict[str, Any]) -> str:
        error = turn.get("error")
        if not isinstance(error, dict):
            return ""
        info = error.get("codexErrorInfo")
        if isinstance(info, str):
            return info
        if isinstance(info, dict) and info:
            return str(next(iter(info)))
        return "codex_turn_failed"
