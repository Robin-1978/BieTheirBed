from __future__ import annotations

import asyncio
import json
import re
import time
from typing import Any, AsyncGenerator, Awaitable, Callable

from pydantic import BaseModel

from pc_assistant.config import AppConfig, load_config
from pc_assistant.context.assembly import assemble_llm_messages, truncate_messages
from pc_assistant.context.conversation import ConversationManager
from pc_assistant.context.evidence import EvidencePolicy
from pc_assistant.context.llm_compact import compact_conversation_llm
from pc_assistant.context.memory import UserMemory
from pc_assistant.context.prompt import build_system_prompt
from pc_assistant.context.token_estimate import TokenEstimator, normalize_family
from pc_assistant.harness.audit import AuditLogger
from pc_assistant.harness.limiter import RateLimiter
from pc_assistant.harness.safety import SafetyChecker
from pc_assistant.llm_provider import LLMProvider, LLMResponse
from pc_assistant.logger import get_logger
from pc_assistant.observability.trace import LLMTraceRecorder, TurnRecorder
from pc_assistant.platform_ import get_platform
from pc_assistant.session import SessionManager, SessionState
from pc_assistant.tools.application import ApplicationTool
from pc_assistant.tools.clipboard import ClipboardTool
from pc_assistant.tools.filesystem import FilesystemTool
from pc_assistant.tools.registry import ToolRegistry
from pc_assistant.tools.shell import ShellTool
from pc_assistant.tools.system import SystemTool
from pc_assistant.tools.web import WebTool
from pc_assistant.tools.memory_tool import MemoryTool
from pc_assistant.tools.weather import WeatherTool
from pc_assistant.tools.exchange import ExchangeTool
from pc_assistant.tools.timer import TimerTool
from pc_assistant.tools.window import WindowTool
from pc_assistant.tools.notification import NotificationTool
from pc_assistant.tools.keyboard import KeyboardTool
from pc_assistant.tools.mouse import MouseTool
from pc_assistant.tools.scheduler import SchedulerTool


class AgentEvent(BaseModel):
    type: str
    content: str = ""
    tool_name: str = ""
    tool_args: dict[str, Any] = {}
    tool_result: Any = None
    blocked: bool = False
    iteration: int = 0


def _strip_think_tags(text: str) -> tuple[str, str]:
    _think_open = re.compile(r"<think[^>]*>", re.IGNORECASE)
    _think_close = re.compile(r"</think[^>]*>", re.IGNORECASE)
    thinking = ""
    remaining = text
    while True:
        m_open = _think_open.search(remaining)
        if m_open is None:
            break
        m_close = _think_close.search(remaining, m_open.end())
        if m_close is None:
            thinking += remaining[m_open.end():]
            remaining = remaining[:m_open.start()]
            break
        thinking += remaining[m_open.end():m_close.start()]
        remaining = remaining[:m_open.start()] + remaining[m_close.end():]
    return remaining.strip(), thinking.strip()


class Agent:
    def __init__(
        self,
        config: AppConfig | None = None,
        confirm_callback: Callable[[str, dict[str, Any]], bool | Awaitable[bool]] | None = None,
        *,
        llm: LLMProvider | None = None,
        conversation: ConversationManager | None = None,
        memory: UserMemory | None = None,
        safety: SafetyChecker | None = None,
        registry: ToolRegistry | None = None,
        limiter: RateLimiter | None = None,
        audit: AuditLogger | None = None,
        max_sessions: int = 100,
        session_manager: SessionManager | None = None,
        trace: LLMTraceRecorder | None = None,
        turn_recorder: TurnRecorder | None = None,
        evidence: EvidencePolicy | None = None,
    ) -> None:
        self._config = config or load_config()
        self._logger = get_logger("agent")
        self._llm = llm if llm is not None else LLMProvider(
            server_url=self._config.llm_server_url,
            model_name=self._config.llm_model_name,
            provider=self._config.llm_provider,
            api_key=self._config.llm_api_key,
            api_base=self._config.llm_api_base,
            timeout=self._config.llm_timeout,
        )
        self._memory = memory if memory is not None else UserMemory()
        self._safety = safety if safety is not None else SafetyChecker(
            dangerous_commands=self._config.dangerous_commands,
            protected_paths=self._config.protected_paths,
        )
        self._registry = registry if registry is not None else ToolRegistry()
        self._limiter = limiter if limiter is not None else RateLimiter()
        self._audit = audit if audit is not None else AuditLogger()
        self._confirm_callback = confirm_callback
        self._current_task: asyncio.Task | None = None
        self._current_status = "ready"
        self._connected = False
        self._system_prompt = build_system_prompt(
            working_directory=self._config.working_directory,
        )
        self._token_estimator = TokenEstimator(
            normalize_family(self._config.token_family, self._config.llm_model_name),
        )

        # Default session state (backward compatible with `agent.conversation`).
        self._default_state = SessionState(
            session_id="",
            conversation=conversation if conversation is not None else ConversationManager(),
        )
        self._default_state.conversation.set_system_context(self._system_prompt)
        self._session_manager = session_manager or SessionManager(
            max_sessions=max(max_sessions, 1),
        )
        self._trace = trace or LLMTraceRecorder(
            path=self._config.llm_trace_log,
            enabled=self._config.trace_enabled,
        )
        self._turn_recorder = turn_recorder or TurnRecorder(
            path=self._config.turn_trace_log,
            enabled=self._config.trace_enabled,
        )
        self._evidence = evidence or EvidencePolicy(enabled=self._config.evidence_policy_enabled)
        self._register_builtin_tools()
        # Loop detection
        self._max_consecutive_same_tool = self._config.max_consecutive_same_tool

    # ------------------------------------------------------------------
    # Backward-compatible attribute mirrors (default session)
    # ------------------------------------------------------------------

    @property
    def conversation(self) -> ConversationManager:
        return self._default_state.conversation

    @property
    def _conversation(self) -> ConversationManager:
        return self._default_state.conversation

    @_conversation.setter
    def _conversation(self, value: ConversationManager) -> None:
        self._default_state.conversation = value

    @property
    def _cancelled(self) -> bool:
        return self._default_state.cancelled

    @_cancelled.setter
    def _cancelled(self, value: bool) -> None:
        self._default_state.cancelled = value

    @property
    def memory(self) -> UserMemory:
        return self._memory

    @property
    def registry(self) -> ToolRegistry:
        return self._registry

    def _get_state(self, session_id: str) -> SessionState:
        if not session_id:
            return self._default_state
        return self._session_manager.get(session_id, self._system_prompt)

    # ------------------------------------------------------------------
    # Cancellation
    # ------------------------------------------------------------------

    def cancel(self, session_id: str = "") -> None:
        state = self._get_state(session_id)
        state.cancelled = True
        self._llm.cancel()
        if self._current_task is not None and not self._current_task.done():
            self._current_task.cancel()

    def cancel_session(self, session_id: str) -> None:
        self.cancel(session_id)

    def reset_cancelled(self) -> None:
        self._default_state.cancelled = False
        self._llm.reset_cancelled()

    # ------------------------------------------------------------------
    # Tool loop detection
    # ------------------------------------------------------------------

    def _check_tool_loop(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        history: list[str],
    ) -> tuple[bool, str]:
        """Check if we're in a tool calling loop. Returns (is_loop, reason).

        Only detects TRUE loops - same tool + same arguments repeatedly.
        Different arguments for same tool is NOT a loop (e.g., checking weather for multiple cities).
        """
        try:
            args_str = json.dumps(arguments, sort_keys=True)
        except Exception:
            args_str = str(sorted(arguments.items()))

        call_sig = f"{tool_name}:{args_str[:200]}"

        history.append(call_sig)
        if len(history) > 20:
            history.pop(0)

        if len(history) >= self._max_consecutive_same_tool:
            recent = history[-self._max_consecutive_same_tool:]
            if all(t == call_sig for t in recent):
                return True, f"Same tool '{tool_name}' with identical arguments called {self._max_consecutive_same_tool} times consecutively"

        return False, ""

    def _smart_truncate(self, result_str: str, tool_name: str, result: Any) -> str:
        """Smart truncation for long tool outputs."""
        max_chars = 3000

        if len(result_str) <= max_chars:
            return result_str

        if tool_name == "application" and isinstance(result, dict):
            processes = result.get("processes", [])
            if processes:
                sorted_by_cpu = sorted(processes, key=lambda x: x.get("cpu_percent", 0) or 0, reverse=True)
                top_cpu = sorted_by_cpu[:10]
                sorted_by_mem = sorted(processes, key=lambda x: x.get("memory_percent", 0) or 0, reverse=True)
                top_mem = sorted_by_mem[:10]

                summary = [
                    f"Total processes: {len(processes)}",
                    "",
                    "Top 10 by CPU%:",
                ]
                for p in top_cpu:
                    name = p.get("name", "unknown")
                    cpu = p.get("cpu_percent", 0)
                    pid = p.get("pid", "?")
                    summary.append(f"  {pid:>6} {name:<30} {cpu:>5.1f}%")

                summary.append("")
                summary.append("Top 10 by Memory%:")
                for p in top_mem:
                    name = p.get("name", "unknown")
                    mem = p.get("memory_percent", 0)
                    pid = p.get("pid", "?")
                    summary.append(f"  {pid:>6} {name:<30} {mem:>5.1f}%")

                summary_str = "\n".join(summary)
                if len(summary_str) <= max_chars:
                    return summary_str + f"\n\n[Truncated: showing top processes. Total: {len(processes)}]"
                return summary_str[:max_chars - 50] + f"\n\n[Truncated from {len(processes)} processes]"

            matches = result.get("matches", [])
            if matches:
                return result_str[:max_chars] + f"\n\n[Showing {len(matches)} matching processes]"

            if result.get("process"):
                return result_str[:max_chars]

        head_size = max_chars * 2 // 3
        tail_size = max_chars - head_size
        omitted = len(result_str) - max_chars
        return (
            result_str[:head_size]
            + f"\n\n... [{omitted} chars omitted] ...\n\n"
            + result_str[-tail_size:]
        )

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    def get_status(self) -> dict[str, Any]:
        state = self._default_state
        return {
            "provider": self._config.llm_provider,
            "model": self._config.llm_model_name or "default",
            "status": self._current_status,
            "connected": self._connected,
            "platform": get_platform(),
            "total_prompt_tokens": state.total_prompt_tokens,
            "total_completion_tokens": state.total_completion_tokens,
            "total_tokens": state.total_prompt_tokens + state.total_completion_tokens,
            "total_iterations": state.total_iterations,
            "conversation_turns": len([m for m in state.conversation.get_messages() if m["role"] == "user"]),
            "memory_items": len(self._memory),
            "tools": self._registry.list_tools(),
            "working_directory": self._config.working_directory,
            "active_sessions": len(self._session_manager),
        }

    def session_stats(self) -> list[dict[str, Any]]:
        return self._session_manager.stats()

    async def health_check(self) -> bool:
        result = await self._llm.health_check()
        self._connected = result
        return result

    def _register_builtin_tools(self) -> None:
        builtin_tools = [
            FilesystemTool(),
            ShellTool(default_timeout=self._config.shell_timeout),
            ApplicationTool(),
            WebTool(),
            SystemTool(),
            ClipboardTool(),
            MemoryTool(memory=self._memory),
            WeatherTool(),
            ExchangeTool(),
            TimerTool(),
            WindowTool(),
            NotificationTool(),
            KeyboardTool(),
            MouseTool(),
            SchedulerTool(),
        ]
        for tool in builtin_tools:
            self._registry.register(tool)

        scheduler = self._registry.get("scheduler")
        if scheduler is not None:
            scheduler.set_agent(self)

    def register_tool(self, tool: Any) -> None:
        self._registry.register(tool)

    @staticmethod
    def _ensure_system_first(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not messages:
            return messages
        system_msgs = [m for m in messages if m.get("role") == "system"]
        other_msgs = [m for m in messages if m.get("role") != "system"]
        return system_msgs + other_msgs

    # ------------------------------------------------------------------
    # Observability helpers
    # ------------------------------------------------------------------

    def _record_llm_call(
        self,
        state: SessionState,
        *,
        iteration: int,
        prompt_tokens: int,
        completion_tokens: int,
        latency_ms: float,
        ttft_ms: float,
        finish_reason: str,
        tool_calls: int,
        error: str,
    ) -> None:
        self._trace.record_call(
            session_id=state.session_id,
            model=self._config.llm_model_name or "default",
            iteration=iteration,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            latency_ms=latency_ms,
            ttft_ms=ttft_ms,
            finish_reason=finish_reason,
            tool_calls=tool_calls,
            error=error,
        )

    def _record_turn(
        self,
        state: SessionState,
        user_input: str,
        *,
        outcome: str,
        evidence_required: bool,
        evidence_satisfied: bool,
        elapsed_ms: float,
    ) -> None:
        self._turn_recorder.record_turn(
            session_id=state.session_id,
            user_input=user_input,
            outcome=outcome,
            iterations=state.total_iterations,
            tool_calls=len(state.tool_call_history),
            prompt_tokens=state.total_prompt_tokens,
            completion_tokens=state.total_completion_tokens,
            elapsed_ms=elapsed_ms,
            evidence_required=evidence_required,
            evidence_satisfied=evidence_satisfied,
        )

    async def _compaction_llm_call(self, messages: list[dict[str, Any]]) -> str:
        resp: LLMResponse = await self._llm.chat(messages, tools=None, max_tokens=512)
        if resp.finish_reason == "error" or resp.content.startswith("LLM request failed"):
            raise RuntimeError(resp.content)
        return resp.content

    # ------------------------------------------------------------------
    # Public entry points
    # ------------------------------------------------------------------

    async def run(self, user_input: str, *, session_id: str = "") -> AsyncGenerator[AgentEvent, None]:
        if not self._limiter.is_allowed("agent"):
            yield AgentEvent(
                type="error",
                content="Rate limit exceeded. Please wait before sending another message.",
            )
            return

        state = self._get_state(session_id)
        state.cancelled = False
        state.tool_call_history.clear()
        state.last_outcome = "running"
        state.mark_snapshot()

        turn_start = time.monotonic()
        evidence_required = self._evidence.requires_evidence(user_input)
        evidence_tool_calls = 0
        try:
            async for event in self._run_loop(state, user_input, evidence_required=evidence_required):
                if event.type == "tool_call" and not event.blocked:
                    evidence_tool_calls += 1
                yield event
        finally:
            outcome = state.last_outcome
            if outcome in ("cancelled", "error"):
                state.rollback_if_needed()
            else:
                state.snapshot_len = -1
            self._record_turn(
                state,
                user_input,
                outcome=outcome,
                evidence_required=evidence_required,
                evidence_satisfied=evidence_tool_calls > 0,
                elapsed_ms=(time.monotonic() - turn_start) * 1000,
            )

    async def _run_loop(
        self,
        state: SessionState,
        user_input: str,
        *,
        evidence_required: bool,
    ) -> AsyncGenerator[AgentEvent, None]:
        conv = state.conversation
        conv.add_user(user_input)

        memory_context = self._memory.build_context_string()
        conv.set_system_context(self._system_prompt)

        if self._config.llm_compact_enabled:
            compacted = await compact_conversation_llm(
                conv.get_messages_for_llm_raw(),
                keep_recent=2,
                llm_call=self._compaction_llm_call,
            )
            if compacted is not None:
                conv.rebuild_from_messages(compacted)

        empty_response_count = 0
        max_empty_retries = 1
        total_tool_calls = 0
        max_total_tool_calls = self._config.max_total_tool_calls
        consecutive_tool_without_answer = 0
        max_consecutive_tool_calls = self._config.max_consecutive_tool_calls
        turn_tool_calls = 0

        system_prompt = self._system_prompt
        if evidence_required:
            system_prompt = system_prompt + "\n\n" + self._evidence.build_instruction()

        for iteration in range(self._config.max_iterations):
            if state.cancelled:
                state.last_outcome = "cancelled"
                yield AgentEvent(type="cancelled", content="Operation cancelled by user.", iteration=iteration)
                self._current_status = "ready"
                return

            self._current_status = "thinking"
            state.total_iterations += 1

            raw_messages = conv.get_messages_for_llm_raw()
            messages = assemble_llm_messages(
                system_prompt,
                raw_messages,
                user_input,
                working_directory=self._config.working_directory,
                memory_context=memory_context,
            )
            messages = truncate_messages(
                messages,
                budget=self._config.context_window_budget,
            )
            messages = self._ensure_system_first(messages)
            tools = self._registry.all_schemas() if len(self._registry) > 0 else None

            full_content = ""
            emitted_clean_len = 0
            emitted_think_len = 0
            tool_calls_from_stream: list[dict[str, Any]] = []
            accumulated_tool_calls: dict[int, dict[str, Any]] = {}
            finish_reason = ""
            stream_had_error = False

            yield AgentEvent(type="stream_start", iteration=iteration)

            llm_start = time.monotonic()
            llm_ttft: float | None = None
            call_usage: dict[str, Any] = {}
            call_error = ""

            try:
                async for chunk in self._llm.chat_stream(messages, tools=tools, max_tokens=self._config.max_tokens):
                    if llm_ttft is None:
                        llm_ttft = (time.monotonic() - llm_start) * 1000

                    if state.cancelled:
                        state.last_outcome = "cancelled"
                        yield AgentEvent(type="cancelled", content="Operation cancelled by user.", iteration=iteration)
                        self._current_status = "ready"
                        return

                    if chunk.finish_reason == "error":
                        yield AgentEvent(
                            type="error",
                            content=chunk.delta_content,
                            iteration=iteration,
                        )
                        stream_had_error = True
                        break

                    if chunk.delta_content:
                        full_content += chunk.delta_content
                        clean_part, think_part = _strip_think_tags(full_content)

                        new_content = clean_part[emitted_clean_len:]
                        if new_content:
                            yield AgentEvent(type="stream_delta", content=new_content, iteration=iteration)
                            emitted_clean_len = len(clean_part)

                        new_think = think_part[emitted_think_len:]
                        if new_think:
                            yield AgentEvent(type="stream_think_delta", content=new_think, iteration=iteration)
                            emitted_think_len = len(think_part)

                    if chunk.delta_thinking:
                        yield AgentEvent(type="stream_think_delta", content=chunk.delta_thinking, iteration=iteration)

                    if chunk.delta_tool_calls:
                        for dtc in chunk.delta_tool_calls:
                            idx = dtc.get("index", len(accumulated_tool_calls))
                            func_delta = dtc.get("function", {})
                            delta_args = func_delta.get("arguments", "")

                            if isinstance(delta_args, dict):
                                accumulated_tool_calls[idx] = {
                                    "id": dtc.get("id", ""),
                                    "type": "function",
                                    "function": {
                                        "name": func_delta.get("name", ""),
                                        "arguments": delta_args,
                                    },
                                }
                                continue

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
                            if func_delta.get("name"):
                                acc["function"]["name"] += func_delta["name"]
                            if delta_args:
                                if isinstance(acc["function"]["arguments"], str):
                                    acc["function"]["arguments"] += delta_args
                                else:
                                    acc["function"]["arguments"] = delta_args

                    if chunk.finish_reason:
                        finish_reason = chunk.finish_reason

                    if chunk.usage:
                        call_usage = {**call_usage, **chunk.usage}
                        prompt_tokens = chunk.usage.get("prompt_tokens") or chunk.usage.get("input_tokens") or 0
                        completion_tokens = chunk.usage.get("completion_tokens") or chunk.usage.get("output_tokens") or 0
                        state.total_prompt_tokens += int(prompt_tokens)
                        state.total_completion_tokens += int(completion_tokens)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                call_error = str(e)
                error_msg = str(e)
                if "timeout" in error_msg.lower() or "timed out" in error_msg.lower():
                    error_msg = (
                        f"LLM request timed out after {self._config.llm_timeout}s. "
                        "Possible causes: prompt too long, model is busy, or server overloaded. "
                        "You can increase PC_LLM_TIMEOUT in config if needed."
                    )
                yield AgentEvent(
                    type="error",
                    content=f"LLM stream error: {error_msg}",
                    iteration=iteration,
                )
                stream_had_error = True

            self._record_llm_call(
                state,
                iteration=iteration,
                prompt_tokens=call_usage.get("prompt_tokens") or call_usage.get("input_tokens") or 0,
                completion_tokens=call_usage.get("completion_tokens") or call_usage.get("output_tokens") or 0,
                latency_ms=(time.monotonic() - llm_start) * 1000,
                ttft_ms=llm_ttft or 0.0,
                finish_reason=finish_reason,
                tool_calls=len(accumulated_tool_calls),
                error=call_error,
            )

            if stream_had_error:
                state.last_outcome = "error"
                self._current_status = "ready"
                return

            self._connected = True

            if accumulated_tool_calls:
                final_tool_calls = []
                for idx in sorted(accumulated_tool_calls.keys()):
                    tc = accumulated_tool_calls[idx]
                    if "function" in tc and isinstance(tc["function"].get("arguments"), str):
                        try:
                            tc["function"]["arguments"] = json.loads(tc["function"]["arguments"])
                        except (json.JSONDecodeError, TypeError):
                            tc["function"]["arguments"] = {}
                    final_tool_calls.append(tc)
                tool_calls_from_stream = final_tool_calls

            clean_content = _strip_think_tags(full_content)[0]

            yield AgentEvent(type="stream_end", iteration=iteration)

            if tool_calls_from_stream:
                stored_content = clean_content.strip()
                conv.add_assistant(stored_content, delta_tool_calls=tool_calls_from_stream)
                for tool_call in tool_calls_from_stream:
                    if state.cancelled:
                        state.last_outcome = "cancelled"
                        yield AgentEvent(type="cancelled", content="Operation cancelled by user.", iteration=iteration)
                        self._current_status = "ready"
                        return

                    func = tool_call.get("function", {})
                    tool_name = func.get("name", "")
                    arguments = func.get("arguments", {})
                    tool_call_id = tool_call.get("id", "")

                    if isinstance(arguments, str):
                        try:
                            arguments = json.loads(arguments)
                        except (json.JSONDecodeError, TypeError):
                            arguments = {}

                    if not isinstance(arguments, dict):
                        arguments = {}

                    is_loop, loop_reason = self._check_tool_loop(tool_name, arguments, state.tool_call_history)
                    if is_loop:
                        yield AgentEvent(
                            type="tool_result",
                            tool_name=tool_name,
                            tool_args=arguments,
                            tool_result={"error": f"Loop detected: {loop_reason}"},
                            content=f"Stopped: {loop_reason}",
                            iteration=iteration,
                        )
                        conv.add_tool_result(tool_call_id, f"Stopped: {loop_reason}", tool_name=tool_name)
                        state.tool_call_history.clear()
                        break

                    safety_result = self._safety.check_tool_call(tool_name, arguments)
                    need_confirm, confirm_reason = self._safety.needs_confirmation(tool_name, arguments)

                    blocked = False
                    block_reason = ""

                    if not safety_result:
                        block_reason = safety_result.reason
                        if self._confirm_callback is not None:
                            try:
                                cb_result = self._confirm_callback(tool_name, arguments)
                                if asyncio.iscoroutine(cb_result):
                                    user_confirmed = await cb_result
                                else:
                                    user_confirmed = cb_result
                            except Exception:
                                user_confirmed = False
                            if user_confirmed:
                                self._audit.log(
                                    action="tool_call_confirmed",
                                    tool=tool_name,
                                    parameters=arguments,
                                    allowed=True,
                                    reason=f"User confirmed override of: {safety_result.reason}",
                                )
                            else:
                                blocked = True
                        else:
                            blocked = True
                    elif need_confirm:
                        block_reason = confirm_reason
                        if self._confirm_callback is not None:
                            try:
                                cb_result = self._confirm_callback(tool_name, arguments)
                                if asyncio.iscoroutine(cb_result):
                                    user_confirmed = await cb_result
                                else:
                                    user_confirmed = cb_result
                            except Exception:
                                user_confirmed = False
                            if not user_confirmed:
                                blocked = True
                                self._audit.log(
                                    action="tool_call_blocked",
                                    tool=tool_name,
                                    parameters=arguments,
                                    allowed=False,
                                    reason=confirm_reason,
                                )
                            else:
                                self._audit.log(
                                    action="tool_call_confirmed",
                                    tool=tool_name,
                                    parameters=arguments,
                                    allowed=True,
                                    reason=f"User confirmed: {confirm_reason}",
                                )
                        else:
                            self._audit.log(
                                action="tool_call",
                                tool=tool_name,
                                parameters=arguments,
                                allowed=True,
                                reason=f"No confirmation callback; proceeding with: {confirm_reason}",
                            )

                    if blocked:
                        self._audit.log(
                            action="tool_call_blocked",
                            tool=tool_name,
                            parameters=arguments,
                            allowed=False,
                            reason=block_reason,
                        )
                        yield AgentEvent(
                            type="tool_call",
                            tool_name=tool_name,
                            tool_args=arguments,
                            blocked=True,
                            content=block_reason,
                            iteration=iteration,
                        )
                        conv.add_tool_result(
                            tool_call_id,
                            f"Blocked: {block_reason}",
                            tool_name=tool_name,
                        )
                        continue

                    yield AgentEvent(
                        type="tool_call",
                        tool_name=tool_name,
                        tool_args=arguments,
                        iteration=iteration,
                    )

                    self._audit.log(
                        action="tool_call",
                        tool=tool_name,
                        parameters=arguments,
                        allowed=True,
                    )

                    total_tool_calls += 1
                    consecutive_tool_without_answer += 1
                    turn_tool_calls += 1

                    if total_tool_calls >= max_total_tool_calls:
                        state.last_outcome = "limit"
                        yield AgentEvent(
                            type="iteration_limit",
                            content=f"Total tool call limit reached ({max_total_tool_calls}).",
                            iteration=iteration,
                        )
                        self._current_status = "ready"
                        return

                    if consecutive_tool_without_answer >= max_consecutive_tool_calls:
                        state.last_outcome = "limit"
                        yield AgentEvent(
                            type="iteration_limit",
                            content=f"Too many tool calls ({consecutive_tool_without_answer}) without producing an answer.",
                            iteration=iteration,
                        )
                        self._current_status = "ready"
                        return

                    self._current_status = f"executing_{tool_name}"

                    try:
                        execute_coro = self._registry.execute(tool_name, **arguments)
                        self._current_task = asyncio.create_task(execute_coro)
                        try:
                            result = await self._current_task
                        except asyncio.CancelledError:
                            if state.cancelled:
                                self._current_status = "ready"
                                state.last_outcome = "cancelled"
                                yield AgentEvent(type="cancelled", content="Operation cancelled by user.", iteration=iteration)
                                return
                            raise
                        self._current_task = None
                        result_str = str(result)
                        result_str = self._smart_truncate(result_str, tool_name, result)
                        conv.add_tool_result(tool_call_id, result_str, tool_name=tool_name)
                        self._current_status = "thinking"
                        yield AgentEvent(
                            type="tool_result",
                            tool_name=tool_name,
                            tool_args=arguments,
                            tool_result=result,
                            content=result_str,
                            iteration=iteration,
                        )
                    except asyncio.CancelledError:
                        self._current_status = "ready"
                        state.last_outcome = "cancelled"
                        return
                    except Exception as e:
                        error_msg = f"Error: {e}"
                        conv.add_tool_result(tool_call_id, error_msg, tool_name=tool_name)
                        self._current_status = "thinking"
                        yield AgentEvent(
                            type="tool_result",
                            tool_name=tool_name,
                            tool_args=arguments,
                            tool_result={"error": str(e)},
                            content=error_msg,
                            iteration=iteration,
                        )
            else:
                consecutive_tool_without_answer = 0

                if finish_reason == "length" and clean_content:
                    if evidence_required and turn_tool_calls == 0:
                        yield AgentEvent(
                            type="evidence_warning",
                            content="Final answer not verified against tool results.",
                            iteration=iteration,
                        )
                    conv.add_assistant_final(clean_content)
                    yield AgentEvent(
                        type="final_answer",
                        content=clean_content,
                        iteration=iteration,
                    )
                    state.last_outcome = "answer"
                    self._current_status = "ready"
                    return

                if not clean_content:
                    empty_response_count += 1
                    if empty_response_count > max_empty_retries:
                        conv.add_assistant_final("I was unable to generate a response. Please try again.")
                        yield AgentEvent(
                            type="final_answer",
                            content="I was unable to generate a response. Please try again.",
                            iteration=iteration,
                        )
                        state.last_outcome = "answer"
                        self._current_status = "ready"
                        return
                    conv.add_user("[System] You did not produce any output. Please respond to the user's question directly.")
                    continue

                empty_response_count = 0
                if evidence_required and turn_tool_calls == 0:
                    yield AgentEvent(
                        type="evidence_warning",
                        content="Final answer not verified against tool results.",
                        iteration=iteration,
                    )
                conv.add_assistant_final(clean_content)
                yield AgentEvent(
                    type="final_answer",
                    content=clean_content,
                    iteration=iteration,
                )
                state.last_outcome = "answer"
                self._current_status = "ready"
                return

        state.last_outcome = "limit"
        yield AgentEvent(
            type="iteration_limit",
            content="Maximum iterations reached without a final answer.",
            iteration=self._config.max_iterations,
        )
        self._current_status = "ready"

    async def run_simple(self, user_input: str) -> str:
        final_answer = ""
        async for event in self.run(user_input):
            if event.type == "final_answer":
                final_answer = event.content
            elif event.type == "error":
                final_answer = event.content
            elif event.type == "iteration_limit":
                final_answer = event.content
            elif event.type == "cancelled":
                final_answer = event.content
        return final_answer

    def reset_conversation(self) -> None:
        self._default_state.conversation.clear()
        self._system_prompt = build_system_prompt(
            working_directory=self._config.working_directory,
        )
        self._default_state.conversation.set_system_context(self._system_prompt)
