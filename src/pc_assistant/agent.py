from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime
from typing import Any, AsyncGenerator, Callable

from pydantic import BaseModel

from pc_assistant.config import AppConfig, load_config
from pc_assistant.context.conversation import ConversationManager
from pc_assistant.context.memory import UserMemory
from pc_assistant.context.system_prompt import build_system_prompt
from pc_assistant.context.truncator import truncate_messages
from pc_assistant.harness.audit import AuditLogger
from pc_assistant.harness.limiter import RateLimiter
from pc_assistant.harness.safety import SafetyChecker
from pc_assistant.llm_provider import LLMProvider, LLMResponse
from pc_assistant.logger import get_logger
from pc_assistant.platform_ import get_platform
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


_THINK_OPEN = re.compile(r"<think[^>]*>", re.IGNORECASE)
_THINK_CLOSE = re.compile(r"</think\s*>", re.IGNORECASE)


def _strip_think_tags(text: str) -> tuple[str, str]:
    thinking = ""
    remaining = text
    while True:
        m_open = _THINK_OPEN.search(remaining)
        if m_open is None:
            break
        m_close = _THINK_CLOSE.search(remaining, m_open.end())
        if m_close is None:
            thinking += remaining[m_open.end():]
            remaining = remaining[:m_open.start()]
            break
        thinking += remaining[m_open.end():m_close.start()]
        remaining = remaining[:m_open.start()] + remaining[m_close.end():]
    return remaining.strip(), thinking.strip()




def _build_date_context() -> str:
    now = datetime.now()
    weekday_names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    return f"Current date: {now.strftime('%Y-%m-%d')} ({weekday_names[now.weekday()]})\nCurrent time: {now.strftime('%H:%M:%S')}"


class _ThinkStreamParser:
    def __init__(self) -> None:
        self._in_think = False
        self._buffer = ""
        self._think_content = ""
        self._clean_content = ""

    @property
    def in_think(self) -> bool:
        return self._in_think

    @property
    def think_content(self) -> str:
        return self._think_content

    @property
    def clean_content(self) -> str:
        return self._clean_content

    def feed(self, delta: str) -> list[tuple[str, str]]:
        self._buffer += delta
        events: list[tuple[str, str]] = []

        while self._buffer:
            if self._in_think:
                close_match = _THINK_CLOSE.search(self._buffer)
                if close_match:
                    text = self._buffer[:close_match.start()]
                    if text:
                        self._think_content += text
                        events.append(("stream_think_delta", text))
                    self._in_think = False
                    self._buffer = self._buffer[close_match.end():]
                    events.append(("think_end", ""))
                else:
                    if _THINK_OPEN.search(self._buffer):
                        potential_partial = self._buffer
                        self._buffer = ""
                        break
                    text = self._buffer
                    self._think_content += text
                    if text:
                        events.append(("stream_think_delta", text))
                    self._buffer = ""
            else:
                open_match = _THINK_OPEN.search(self._buffer)
                if open_match:
                    before = self._buffer[:open_match.start()]
                    if before:
                        self._clean_content += before
                        events.append(("stream_delta", before))
                    self._in_think = True
                    self._buffer = self._buffer[open_match.end():]
                    events.append(("think_start", ""))
                else:
                    if "</think" in self._buffer or "<thi" in self._buffer:
                        potential_partial = self._buffer
                        self._buffer = ""
                        break
                    text = self._buffer
                    self._clean_content += text
                    if text:
                        events.append(("stream_delta", text))
                    self._buffer = ""

        return events

    def flush(self) -> list[tuple[str, str]]:
        events: list[tuple[str, str]] = []
        if self._buffer:
            if self._in_think:
                self._think_content += self._buffer
                events.append(("stream_think_delta", self._buffer))
            else:
                self._clean_content += self._buffer
                events.append(("stream_delta", self._buffer))
            self._buffer = ""
        return events


class Agent:
    def __init__(
        self,
        config: AppConfig | None = None,
        confirm_callback: Callable[[str, dict[str, Any]], bool] | None = None,
    ) -> None:
        self._config = config or load_config()
        self._logger = get_logger("agent")
        self._llm = LLMProvider(
            server_url=self._config.llm_server_url,
            model_name=self._config.llm_model_name,
            provider=self._config.llm_provider,
            api_key=self._config.llm_api_key,
            api_base=self._config.llm_api_base,
            timeout=self._config.llm_timeout,
        )
        self._conversation = ConversationManager()
        self._memory = UserMemory()
        self._safety = SafetyChecker(
            dangerous_commands=self._config.dangerous_commands,
            protected_paths=self._config.protected_paths,
        )
        self._registry = ToolRegistry(safety=self._safety)
        self._limiter = RateLimiter()
        self._audit = AuditLogger()
        self._confirm_callback = confirm_callback
        self._cancelled = False
        self._current_task: asyncio.Task | None = None
        self._total_prompt_tokens = 0
        self._total_completion_tokens = 0
        self._total_iterations = 0
        self._current_status = "ready"
        self._connected = False
        self._system_prompt = build_system_prompt(
            working_directory=self._config.working_directory,
        )
        self._conversation.set_system_context(self._system_prompt)
        self._register_builtin_tools()
        # Loop detection
        self._tool_call_history: list[str] = []
        self._max_consecutive_same_tool = 3

    @property
    def conversation(self) -> ConversationManager:
        return self._conversation

    @property
    def memory(self) -> UserMemory:
        return self._memory

    @property
    def registry(self) -> ToolRegistry:
        return self._registry

    def cancel(self) -> None:
        self._cancelled = True
        self._llm.cancel()
        if self._current_task is not None and not self._current_task.done():
            self._current_task.cancel()

    def _check_tool_loop(self, tool_name: str, arguments: dict[str, Any]) -> tuple[bool, str]:
        """Check if we're in a tool calling loop. Returns (is_loop, reason).

        Only detects TRUE loops - same tool + same arguments repeatedly.
        Different arguments for same tool is NOT a loop (e.g., checking weather for multiple cities).
        """
        # Create signature based on tool name AND arguments
        # Normalize arguments for comparison
        try:
            import json
            args_str = json.dumps(arguments, sort_keys=True)
        except:
            args_str = str(sorted(arguments.items()))

        call_sig = f"{tool_name}:{args_str[:200]}"

        # Track recent tool calls
        self._tool_call_history.append(call_sig)
        if len(self._tool_call_history) > 20:
            self._tool_call_history.pop(0)

        # Only loop if EXACTLY the same call is made repeatedly (same tool + same args)
        # This is different from calling the same tool with different arguments
        if len(self._tool_call_history) >= 5:
            recent = self._tool_call_history[-5:]
            if all(t == call_sig for t in recent):
                return True, f"Same tool '{tool_name}' with identical arguments called 5 times consecutively"

        return False, ""

    def _smart_truncate(self, result_str: str, tool_name: str, result: Any) -> str:
        """Smart truncation for long tool outputs.

        For process lists and similar outputs, provide a useful summary
        instead of just head+tail truncation.
        """
        max_chars = 3000

        if len(result_str) <= max_chars:
            return result_str

        # Handle application tool specially
        if tool_name == "application" and isinstance(result, dict):
            # list_running action
            processes = result.get("processes", [])
            if processes:
                # Get top processes by CPU usage
                sorted_by_cpu = sorted(processes, key=lambda x: x.get("cpu_percent", 0) or 0, reverse=True)
                top_cpu = sorted_by_cpu[:10]
                # Get top processes by memory usage
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

            # search action - already filtered, show all matches
            matches = result.get("matches", [])
            if matches:
                return result_str[:max_chars] + f"\n\n[Showing {len(matches)} matching processes]"

            # info action - should be small, just truncate if needed
            if result.get("process"):
                return result_str[:max_chars]

        # Default head+tail truncation for other outputs
        head_size = max_chars * 2 // 3
        tail_size = max_chars - head_size
        omitted = len(result_str) - max_chars
        return (
            result_str[:head_size]
            + f"\n\n... [{omitted} chars omitted] ...\n\n"
            + result_str[-tail_size:]
        )

    def get_status(self) -> dict[str, Any]:
        return {
            "provider": self._config.llm_provider,
            "model": self._config.llm_model_name or "default",
            "status": self._current_status,
            "connected": self._connected,
            "platform": get_platform(),
            "total_prompt_tokens": self._total_prompt_tokens,
            "total_completion_tokens": self._total_completion_tokens,
            "total_tokens": self._total_prompt_tokens + self._total_completion_tokens,
            "total_iterations": self._total_iterations,
            "conversation_turns": len([m for m in self._conversation.get_messages() if m["role"] == "user"]),
            "memory_items": len(self._memory),
            "tools": self._registry.list_tools(),
            "working_directory": self._config.working_directory,
        }

    async def health_check(self) -> bool:
        result = await self._llm.health_check()
        self._connected = result
        return result

    def _register_builtin_tools(self) -> None:
        builtin_tools = [
            FilesystemTool(),
            ShellTool(),
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

        # Set agent reference for scheduler to enable dynamic task execution
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

    async def _call_llm_with_retry(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        max_retries: int = 3,
    ) -> LLMResponse | None:
        last_error: Exception | None = None
        for attempt in range(max_retries):
            try:
                return await self._llm.chat(messages, tools=tools)
            except Exception as e:
                last_error = e
                if attempt < max_retries - 1:
                    import asyncio
                    await asyncio.sleep(0.5 * (2 ** attempt))
        if last_error is not None:
            return LLMResponse(content=f"LLM request failed after retries: {last_error}", finish_reason="error")
        return None

    async def run(self, user_input: str) -> AsyncGenerator[AgentEvent, None]:
        if not self._limiter.is_allowed("agent"):
            yield AgentEvent(
                type="error",
                content="Rate limit exceeded. Please wait before sending another message.",
            )
            return

        self._cancelled = False
        self._current_status = "thinking"
        self._conversation.add_user(user_input)

        # Reset loop detection for new task
        self._tool_call_history.clear()

        memory_context = self._memory.build_context_string()
        if memory_context:
            full_system = self._system_prompt + "\n\n" + memory_context
        else:
            full_system = self._system_prompt
        self._conversation.set_system_context(full_system)

        empty_response_count = 0
        max_empty_retries = 2

        for iteration in range(self._config.max_iterations):
            if self._cancelled:
                yield AgentEvent(type="cancelled", content="Operation cancelled by user.", iteration=iteration)
                self._current_status = "ready"
                return

            self._current_status = "thinking"
            self._total_iterations += 1

            messages = self._conversation.get_messages_for_llm()
            messages = truncate_messages(
                messages,
                budget=self._config.context_window_budget,
            )
            messages = self._ensure_system_first(messages)
            tools = self._registry.all_schemas() if len(self._registry) > 0 else None

            full_content = ""
            tool_calls_from_stream: list[dict[str, Any]] = []
            finish_reason = ""
            stream_had_error = False
            think_parser = _ThinkStreamParser()
            thinking_from_field = False

            yield AgentEvent(type="stream_start", iteration=iteration)

            try:
                async for chunk in self._llm.chat_stream(messages, tools=tools, max_tokens=self._config.max_tokens):
                    if self._cancelled:
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

                    if chunk.delta_thinking:
                        if not thinking_from_field:
                            thinking_from_field = True
                            yield AgentEvent(type="think_start", iteration=iteration)
                        yield AgentEvent(
                            type="stream_think_delta",
                            content=chunk.delta_thinking,
                            iteration=iteration,
                        )

                    if chunk.delta_content:
                        full_content += chunk.delta_content
                        parsed_events = think_parser.feed(chunk.delta_content)
                        for evt_type, evt_content in parsed_events:
                            yield AgentEvent(
                                type=evt_type,
                                content=evt_content,
                                iteration=iteration,
                            )

                    if chunk.delta_tool_calls:
                        tool_calls_from_stream = chunk.delta_tool_calls

                    if chunk.finish_reason:
                        finish_reason = chunk.finish_reason

                    if chunk.usage:
                        prompt_tokens = chunk.usage.get("prompt_tokens") or chunk.usage.get("input_tokens") or 0
                        completion_tokens = chunk.usage.get("completion_tokens") or chunk.usage.get("output_tokens") or 0
                        self._total_prompt_tokens += int(prompt_tokens)
                        self._total_completion_tokens += int(completion_tokens)
            except Exception as e:
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

            if stream_had_error:
                self._current_status = "ready"
                return

            self._connected = True

            remaining_events = think_parser.flush()
            for evt_type, evt_content in remaining_events:
                yield AgentEvent(type=evt_type, content=evt_content, iteration=iteration)

            if think_parser.in_think:
                yield AgentEvent(type="think_end", content="", iteration=iteration)

            if thinking_from_field:
                yield AgentEvent(type="think_end", content="", iteration=iteration)

            clean_content, thinking_content = _strip_think_tags(full_content)

            yield AgentEvent(type="stream_end", iteration=iteration)

            if thinking_content:
                yield AgentEvent(
                    type="thought",
                    content=thinking_content,
                    iteration=iteration,
                )

            if tool_calls_from_stream:
                # Store content without tool_calls to prevent AI confusion in history
                # The tool_calls were already sent and results will follow
                self._conversation.add_assistant_final(clean_content or full_content)
                for tool_call in tool_calls_from_stream:
                    if self._cancelled:
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

                    # Check for tool calling loops
                    is_loop, loop_reason = self._check_tool_loop(tool_name, arguments)
                    if is_loop:
                        yield AgentEvent(
                            type="tool_result",
                            tool_name=tool_name,
                            tool_args=arguments,
                            tool_result={"error": f"Loop detected: {loop_reason}"},
                            content=f"Stopped: {loop_reason}",
                            iteration=iteration,
                        )
                        self._conversation.add_tool_result(tool_call_id, f"Stopped: {loop_reason}")
                        # Clear history to allow recovery
                        self._tool_call_history.clear()
                        break

                    safety_result = self._safety.check_tool_call(tool_name, arguments)

                    if not safety_result:
                        user_confirmed = False
                        if self._confirm_callback is not None:
                            try:
                                user_confirmed = self._confirm_callback(tool_name, arguments)
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
                            self._audit.log(
                                action="tool_call_blocked",
                                tool=tool_name,
                                parameters=arguments,
                                allowed=False,
                                reason=safety_result.reason,
                            )
                            yield AgentEvent(
                                type="tool_call",
                                tool_name=tool_name,
                                tool_args=arguments,
                                blocked=True,
                                content=safety_result.reason,
                                iteration=iteration,
                            )
                            self._conversation.add_tool_result(
                                tool_call_id,
                                f"Blocked: {safety_result.reason}",
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

                    self._current_status = f"executing_{tool_name}"

                    try:
                        self._current_task = asyncio.create_task(
                            self._registry.execute(tool_name, **arguments)
                        )
                        # Wait for task with cancellation check
                        while not self._current_task.done():
                            if self._cancelled:
                                self._current_task.cancel()
                                try:
                                    await self._current_task
                                except asyncio.CancelledError:
                                    pass
                                self._current_status = "ready"
                                yield AgentEvent(type="cancelled", content="Operation cancelled by user.", iteration=iteration)
                                return
                            await asyncio.sleep(0.1)
                        result = await self._current_task
                        result_str = str(result)
                        # Smart truncation for long outputs
                        result_str = self._smart_truncate(result_str, tool_name, result)
                        self._conversation.add_tool_result(tool_call_id, result_str)
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
                        return
                    except Exception as e:
                        error_msg = f"Error: {e}"
                        self._conversation.add_tool_result(tool_call_id, error_msg)
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
                if finish_reason == "length" and clean_content:
                    self._conversation.add_assistant_final(clean_content)
                    yield AgentEvent(
                        type="final_answer",
                        content=clean_content,
                        iteration=iteration,
                    )
                    self._current_status = "ready"
                    return

                if not clean_content and not full_content:
                    empty_response_count += 1
                    if empty_response_count > max_empty_retries:
                        self._conversation.add_assistant_final("I was unable to generate a response. Please try again.")
                        yield AgentEvent(
                            type="final_answer",
                            content="I was unable to generate a response. Please try again.",
                            iteration=iteration,
                        )
                        self._current_status = "ready"
                        return
                    self._conversation.add("user", "[System] You did not produce any output. Please respond to the user's question.")
                    continue

                if not clean_content and full_content:
                    empty_response_count += 1
                    if empty_response_count > max_empty_retries:
                        self._conversation.add_assistant_final("I was unable to generate a visible response. Please try again.")
                        yield AgentEvent(
                            type="final_answer",
                            content="I was unable to generate a visible response. Please try again.",
                            iteration=iteration,
                        )
                        self._current_status = "ready"
                        return
                    self._conversation.add("user", "Please provide your answer based on your thinking.")
                    continue

                empty_response_count = 0
                self._conversation.add_assistant_final(clean_content or full_content)
                yield AgentEvent(
                    type="final_answer",
                    content=clean_content or full_content,
                    iteration=iteration,
                )
                self._current_status = "ready"
                return

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
        self._conversation.clear()
        self._system_prompt = build_system_prompt(
            working_directory=self._config.working_directory,
        )
        memory_context = self._memory.build_context_string()
        if memory_context:
            full_system = self._system_prompt + "\n\n" + memory_context
        else:
            full_system = self._system_prompt
        self._conversation.set_system_context(full_system)
