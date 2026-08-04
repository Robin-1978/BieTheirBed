"""Textual-based chat application for PC Assistant."""
from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Awaitable, Callable

from textual import work
from textual.app import App, ComposeResult
from textual.containers import Vertical, VerticalScroll
from textual.widgets import Header, Markdown, Static

from pc_assistant.agent import Agent, AgentEvent
from pc_assistant.config import AppConfig
from pc_assistant.harness.confirm import CONFIRM_TIMEOUT
from pc_assistant.service.agent_like import AgentLike
from pc_assistant.ui.clipboard import copy_or_save
from pc_assistant.ui.state import UIState, MessageType
from pc_assistant.ui.theme import get_palette, set_theme, AVAILABLE_THEMES
from pc_assistant.ui.widgets import (
    AssistantMessage,
    ChatInput,
    CommandOutput,
    ToolCallPanel,
    UserMessage,
    ICON_ERROR,
    ICON_READY,
    ICON_WARN,
)

_WELCOME_ART = r"""
  ____  _        _   _               ____           _
 |  _ \(_)      | | | |             |  _ \         | |
 | |_) |_  ___  | |_| |__   ___ _ __| |_) | ___  __| |
 |  _ <| |/ _ \ | __| '_ \ / _ \ '__|  _ < / _ \/ _` |
 | |_) | |  __/ | |_| | | |  __/ |  | |_) |  __/ (_| |
 |____/|_|\___|  \__|_| |_|\___|_|  |____/ \___|\__,_|
"""

_WELCOME_MD = """\
```
{art}```

Type a message to chat, or use `/help` for commands.
*Enter* to send \u2022 *Shift+Enter* for newline \u2022 *Ctrl+C* to copy selection \u2022 *Esc* to cancel \u2022 *Ctrl+D* to quit
""".format(art=_WELCOME_ART)

_COMMANDS_HELP = """\
| Command | Description |
|---------|-------------|
| `/exit`, `/quit` | Save conversation and exit |
| `/clear` | Clear conversation history |
| `/memory` | Show remembered user preferences |
| `/memory clear` | Clear all memories |
| `/history` | Show conversation history summary |
| `/tools` | List available tools |
| `/status` | Show detailed agent status |
| `/help` | Show this help message |
| `/config` | Show current configuration |
| `/config set key=value` | Set a config field at runtime |
| `/retry` | Retry the last user input |
| `/export` | Export conversation to file |
| `/compact` | Compact context (clear old messages) |
| `/theme` | List themes or `/theme <name>` to switch |
| `/confirm` | Approve a pending dangerous tool call |
| `/deny` | Reject a pending dangerous tool call |
"""


class ChatApp(App):
    """Full-screen chat TUI built on Textual."""

    CSS_PATH = "chat.tcss"
    TITLE = "PC Assistant"
    BINDINGS = [
        ("escape", "cancel_turn", "Cancel current turn"),
        ("ctrl+d", "quit", "Quit"),
        ("ctrl+shift+c", "copy_last", "Copy selection / last answer"),
    ]

    def get_css_variables(self) -> dict[str, str]:
        """Inject theme palette colors as CSS variables."""
        palette = get_palette()
        return {**super().get_css_variables(), **palette}

    def __init__(
        self,
        config: AppConfig,
        agent: AgentLike | Agent | None = None,
        confirm_callback: Callable[[str, dict[str, Any]], bool | Awaitable[bool]] | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self._config = config
        set_theme(config.ui_theme)
        self._agent: AgentLike | Agent | None = None
        self._confirm_callback = confirm_callback
        self._is_remote = False
        self._state = UIState()
        self._last_input = ""
        self._cancelled = False
        self._processing = False
        self._current_response: AssistantMessage | None = None
        self._current_tool_panel: ToolCallPanel | None = None
        self._md_stream: Any = None
        self._token_count = 0
        self._scroll_pending = False
        self._streamed_any = False
        self._confirm_pending: dict[str, Any] | None = None
        if agent is not None:
            self.set_agent(agent)

    @property
    def state(self) -> UIState:
        return self._state

    def set_agent(self, agent: AgentLike | Agent) -> None:
        self._agent = agent
        self._is_remote = not isinstance(agent, Agent)

    def _make_tui_confirm(self) -> Any:
        """Create a per-turn confirm callback bound to this TUI instance."""
        async def _tui_confirm(tool_name: str, args: dict) -> bool:
            args_str = ", ".join(f"{k}={v}" for k, v in list(args.items())[:3])
            future: asyncio.Future[bool] = asyncio.get_running_loop().create_future()
            self._confirm_pending = {
                "code": "__local__",
                "tool": tool_name,
                "future": future,
            }
            self.call_later(
                self._mount_confirm_prompt, tool_name, args_str,
            )
            try:
                return await asyncio.wait_for(future, timeout=CONFIRM_TIMEOUT)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                return False
            finally:
                if self._confirm_pending is not None:
                    self._confirm_pending = None
        return _tui_confirm

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        yield VerticalScroll(id="chat-log")
        yield Vertical(
            ChatInput(id="user-input"),
            Static(f" {ICON_READY} Ready  |  /help for commands", id="status-bar"),
            id="bottom-bar",
        )

    def on_mount(self) -> None:
        log = self.query_one("#chat-log", VerticalScroll)
        log.mount(CommandOutput(_WELCOME_MD))
        self.query_one("#user-input", ChatInput).focus()
        self._wire_notifications()

    def _wire_notifications(self) -> None:
        """Connect notification callbacks for both in-process and remote agents."""
        if self._agent is None:
            return

        if self._is_remote:
            from pc_assistant.service.client import ServiceClient
            if isinstance(self._agent, ServiceClient):
                self._agent.set_notify_handler(self._on_timer_notify)
                self._agent.set_confirm_handler(self._on_confirm_request)
        else:
            if isinstance(self._agent, Agent):
                scheduler = self._agent.registry.get("scheduler")
                if scheduler is not None:
                    scheduler.set_notification_callback(self._on_timer_notify)

    async def _on_confirm_request(self, data: dict[str, Any]) -> None:
        """Handle confirm_request from the service (for dangerous tool calls).

        Called from ServiceClient._reader_loop via asyncio.create_task, which
        lacks Textual's ``active_app`` ContextVar. DOM operations must be
        scheduled via ``call_later`` to run in the app's own context.
        """
        tool = data.get("tool", "?")
        args = data.get("args", {})
        code = data.get("code", "")

        args_str = ", ".join(f"{k}={v}" for k, v in list(args.items())[:3])
        self._confirm_pending = {"code": code, "tool": tool, "ts": time.time()}
        self.call_later(
            self._mount_confirm_prompt, tool, args_str,
        )
        self.set_timer(CONFIRM_TIMEOUT, self._expire_remote_confirm, code)

    def _expire_remote_confirm(self, code: str) -> None:
        """Clear a stale remote confirmation (server already timed out)."""
        pending = getattr(self, "_confirm_pending", None)
        if pending is None or pending.get("code") != code:
            return
        if "future" in pending:
            return
        self._confirm_pending = None
        self.notify(
            "Confirmation expired (no response within the time limit).",
            title="Confirm",
            severity="warning",
            timeout=6,
        )

    def _mount_confirm_prompt(self, tool: str, args_str: str) -> None:
        """Mount the confirmation prompt (runs in Textual's app context)."""
        log = self.query_one("#chat-log", VerticalScroll)
        log.mount(CommandOutput(
            f"**\u26a0\ufe0f Dangerous operation: {tool}**\n\n"
            f"`{args_str}`\n\n"
            f"Type `/confirm` to approve or `/deny` to reject."
        ))
        self._request_scroll()

    def _on_timer_notify(self, task_id: str, message: str) -> None:
        self.notify(message, title=f"Timer: {task_id}", severity="information", timeout=8)

    # ── Selection copy ──────────────────────────────────────────

    def on_mouse_down(self, event: Any) -> None:
        """Right-click copies the existing Textual selection directly."""
        if getattr(event, "button", 0) != 3:
            return
        selected_text = self._selected_text()
        if not selected_text:
            return
        try:
            event.prevent_default()
            event.stop()
        except Exception:
            pass
        self._copy_worker(selected_text, "Copied selected text")

    def _selected_text(self) -> str:
        try:
            text = self.screen.get_selected_text() or ""
            return text if text.strip() else ""
        except Exception:
            return ""

    def _last_answer_text(self) -> str:
        if self._agent is None:
            return ""
        try:
            messages = self._agent.conversation.get_messages()
        except Exception:
            return ""
        for msg in reversed(messages):
            if msg.get("role") == "assistant":
                return self._format_content(msg.get("content", ""))
        return ""

    def _conversation_text(self) -> str:
        if self._agent is None:
            return ""
        try:
            messages = self._agent.conversation.get_messages()
        except Exception:
            return ""
        lines = []
        for msg in messages:
            role = msg.get("role", "?")
            content = self._format_content(msg.get("content", ""))
            lines.append(f"## {role}\n{content}\n")
        return "\n".join(lines)

    @staticmethod
    def _format_content(content: Any) -> str:
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = []
            for block in content:
                if not isinstance(block, dict):
                    parts.append(str(block))
                    continue
                block_type = block.get("type")
                if block_type == "text":
                    parts.append(block.get("text", ""))
                elif block_type == "image":
                    parts.append("[image]")
                else:
                    parts.append(str(block))
            return "".join(parts)
        return str(content)

    @work(exclusive=False)
    async def _copy_worker(self, text: str, label: str) -> None:
        # OSC 52 reaches the terminal/remote client clipboard; the platform
        # helper below also updates the desktop clipboard when available.
        self.copy_to_clipboard(text)
        ok, detail = await copy_or_save(text)
        detail = f"sent via terminal clipboard (OSC52); {detail}"
        severity = "information" if ok else "warning"
        self.notify(f"{label}: {detail}", title="Copy", severity=severity, timeout=5)

    # ── Input handling ─────────────────────────────────────────

    def on_chat_input_submitted(self, event: ChatInput.Submitted) -> None:
        text = event.value
        if text.startswith("/"):
            self.run_worker(self._handle_command(text))
        else:
            self._last_input = text
            self._run_agent_turn(text)

    # ── Agent turn ─────────────────────────────────────────────

    @work(exclusive=True)
    async def _run_agent_turn(self, text: str) -> None:
        if self._agent is None:
            self._show_error("Agent not initialized.")
            return

        self._cancelled = False
        self._processing = True
        self._streamed_any = False
        self._state.processing = True
        self._update_status("thinking\u2026")

        log = self.query_one("#chat-log", VerticalScroll)

        user_msg = UserMessage(text)
        await log.mount(user_msg)
        self._state.add_message(MessageType.USER, text)

        response = AssistantMessage()
        await log.mount(response)
        self._current_response = response
        self._current_tool_panel = None

        stream = Markdown.get_stream(response.markdown)
        self._md_stream = stream

        confirm_fn = self._make_tui_confirm() if not self._is_remote else None

        try:
            async for event in self._agent.run(text, confirm_callback=confirm_fn):
                if self._cancelled:
                    break
                await self._handle_event(event, response, stream)
        except Exception as e:
            self._show_error(str(e))
        finally:
            await stream.stop()
            self._md_stream = None
            self._current_response = None
            self._processing = False
            self._state.processing = False
            self._update_status_ready()
            log.scroll_end(animate=False)

    async def _handle_event(
        self,
        event: AgentEvent,
        response: AssistantMessage,
        stream: Any,
    ) -> None:
        log = self.query_one("#chat-log", VerticalScroll)

        if event.type == "stream_delta":
            await stream.write(event.content)
            self._streamed_any = True
            self._update_status("generating\u2026")

        elif event.type == "stream_think_delta":
            response.add_thinking(event.content)
            self._update_status("thinking\u2026")

        elif event.type == "tool_call":
            if event.blocked:
                panel = response.add_tool_call(
                    event.tool_name, event.tool_args,
                    blocked=True, block_reason=event.content,
                )
                self._state.add_message(
                    MessageType.TOOL_CALL, f"[blocked] {event.tool_name}",
                    tool_name=event.tool_name, tool_args=event.tool_args,
                )
            else:
                panel = response.add_tool_call(event.tool_name, event.tool_args)
                self._state.add_message(
                    MessageType.TOOL_CALL, f"[{event.tool_name}]",
                    tool_name=event.tool_name, tool_args=event.tool_args,
                )
                self._update_status(event.tool_name)
            self._current_tool_panel = panel

        elif event.type == "tool_result":
            result_str = (
                str(event.tool_result) if event.tool_result is not None else event.content
            )
            is_error = isinstance(event.tool_result, dict) and "error" in event.tool_result
            if self._current_tool_panel is not None:
                self._current_tool_panel.set_result(result_str, is_error=is_error)
                self._current_tool_panel = None
            self._state.add_message(
                MessageType.TOOL_RESULT, result_str[:200],
                tool_name=event.tool_name,
            )

        elif event.type == "artifact" and event.artifact:
            name = event.artifact.name
            artifact_id = event.artifact.artifact_id
            await stream.write(f"\n\n*Artifact available: `{name}` (`{artifact_id}`)*\n")

        elif event.type == "final_answer":
            if not self._streamed_any and event.content:
                await stream.write(event.content)
            self._state.add_message(MessageType.ASSISTANT, event.content)

        elif event.type == "error":
            self._state.add_message(MessageType.ERROR, event.content)
            await stream.write(f"\n\n{ICON_ERROR} **Error:** {event.content}\n")

        elif event.type == "iteration_limit":
            await stream.write(f"\n\n{ICON_WARN} {event.content}\n")

        elif event.type == "cancelled":
            await stream.write(f"\n\n*Cancelled.*\n")

        if self._agent is not None:
            status = await self._agent.get_status()
            self._token_count = status.get("total_tokens", 0)

        self._request_scroll()

    def _request_scroll(self) -> None:
        """Debounced scroll-to-bottom so rapid events don't block the UI."""
        if not self._scroll_pending:
            self._scroll_pending = True
            self.call_later(self._do_scroll)

    def _do_scroll(self) -> None:
        self._scroll_pending = False
        try:
            log = self.query_one("#chat-log", VerticalScroll)
            log.scroll_end(animate=False)
        except Exception:
            pass

    # ── Status bar ─────────────────────────────────────────────

    def _update_status(self, op: str) -> None:
        bar = self.query_one("#status-bar", Static)
        tokens = f"{self._token_count:,}" if self._token_count else "0"
        bar.update(f" \u283b {op}  |  Tokens: {tokens}")

    def _update_status_ready(self) -> None:
        bar = self.query_one("#status-bar", Static)
        tokens = f"{self._token_count:,}" if self._token_count else "0"
        bar.update(f" {ICON_READY} Ready  |  Tokens: {tokens}  |  /help for commands")

    # ── Cancel ─────────────────────────────────────────────────

    def action_cancel_turn(self) -> None:
        if self._processing:
            self._cancelled = True
            if self._agent is not None:
                self._agent.cancel()
        else:
            self.clear_selection()
            try:
                inp = self.query_one("#user-input", ChatInput)
                if inp.text:
                    inp.clear()
            except Exception:
                pass

    def action_copy_last(self) -> None:
        """Copy the mouse selection, falling back to the last answer."""
        selected_text = self._selected_text()
        text = selected_text or self._last_answer_text()
        if text:
            label = "Copied selected text" if selected_text else "Copied last answer"
            self._copy_worker(text, label)
        else:
            self.notify("No assistant answer to copy yet.", title="Copy", severity="warning", timeout=3)

    # ── Error helpers ──────────────────────────────────────────

    def _show_error(self, message: str) -> None:
        self._state.add_message(MessageType.ERROR, message)
        log = self.query_one("#chat-log", VerticalScroll)
        log.mount(CommandOutput(f"{ICON_ERROR} {message}"))

    # ── Slash commands ─────────────────────────────────────────

    async def _handle_command(self, command: str) -> bool:
        cmd = command.lower().strip()
        log = self.query_one("#chat-log", VerticalScroll)

        if cmd in ("/exit", "/quit"):
            self.exit()
        elif cmd == "/clear":
            try:
                if self._agent is not None:
                    if self._is_remote:
                        from pc_assistant.service.client import ServiceClient

                        if not isinstance(self._agent, ServiceClient):
                            raise RuntimeError("Remote agent does not support session commands")
                        await self._agent.command("/clear")
                    else:
                        if not isinstance(self._agent, Agent):
                            raise RuntimeError("Local agent does not support conversation reset")
                        self._agent.reset_conversation()
            except Exception as exc:
                log.mount(CommandOutput(f"{ICON_ERROR} Clear failed: {exc}"))
                return True
            self._state.clear_messages()
            log.remove_children()
            log.mount(CommandOutput("*Conversation cleared.*"))
        elif cmd == "/help":
            log.mount(CommandOutput(_COMMANDS_HELP))
        elif cmd == "/tools":
            if self._agent is None:
                log.mount(CommandOutput(f"{ICON_WARN} No agent initialized."))
            else:
                tools = self._agent.registry.list_tools()
                if not tools:
                    log.mount(CommandOutput("*No tools registered.*"))
                else:
                    rows = "\n".join(f"| `{t}` |" for t in tools)
                    log.mount(CommandOutput(f"| Tool |\n|------|\n{rows}"))
        elif cmd == "/history":
            if self._agent is None:
                log.mount(CommandOutput(f"{ICON_WARN} No agent initialized."))
            else:
                messages = self._agent.conversation.get_messages()
                if not messages:
                    log.mount(CommandOutput("*No conversation history.*"))
                else:
                    lines = []
                    for i, msg in enumerate(messages):
                        role = msg.get("role", "?")
                        content = msg.get("content", "")[:120]
                        lines.append(f"| {i+1} | {role} | {content} |")
                    header = "| # | Role | Content |\n|---|------|---------|"
                    log.mount(CommandOutput(f"{header}\n" + "\n".join(lines)))
        elif cmd == "/status":
            if self._agent is None:
                log.mount(CommandOutput(f"{ICON_WARN} No agent initialized."))
            else:
                status = await self._agent.get_status()
                rows = "\n".join(f"| {k} | {v} |" for k, v in status.items()
                                 if not isinstance(v, list))
                log.mount(CommandOutput(f"| Property | Value |\n|----------|-------|\n{rows}"))
        elif cmd == "/memory clear":
            if self._agent is not None:
                self._agent.memory.clear()
            log.mount(CommandOutput("*All memories cleared.*"))
        elif cmd == "/memory":
            if self._agent is None:
                log.mount(CommandOutput(f"{ICON_WARN} No agent initialized."))
            else:
                items = self._agent.memory.get_all()
                if not items:
                    log.mount(CommandOutput("*No memories stored.*"))
                else:
                    rows = "\n".join(
                        f"| {it.category} | {it.key} | {it.value[:60]} |"
                        for it in sorted(items, key=lambda x: x.category)
                    )
                    log.mount(CommandOutput(
                        f"| Category | Key | Value |\n|----------|-----|-------|\n{rows}"
                    ))
        elif cmd.startswith("/config set "):
            parts = command.strip().split(None, 2)
            if len(parts) >= 3 and "=" in parts[2]:
                field_name, field_value = parts[2].split("=", 1)
                field_name = field_name.strip()
                field_value = field_value.strip()
                if self._config.set_field(field_name, field_value):
                    display = "****" if field_name == "llm_api_key" else field_value
                    log.mount(CommandOutput(f"Set `{field_name}` = `{display}`"))
                else:
                    log.mount(CommandOutput(f"{ICON_WARN} Unknown config field: `{field_name}`"))
            else:
                log.mount(CommandOutput(f"{ICON_WARN} Usage: `/config set key=value`"))
        elif cmd == "/config":
            from pc_assistant.ui.theme import get_theme_name
            rows = "\n".join([
                f"| Provider | {self._config.llm_provider} |",
                f"| Server | {self._config.llm_server_url} |",
                f"| Model | {self._config.llm_model_name or '(default)'} |",
                f"| API Key | {self._config.masked_api_key()} |",
                f"| Max Iterations | {self._config.max_iterations} |",
                f"| Context Budget | {self._config.context_window_budget} |",
                f"| Theme | {get_theme_name()} |",
            ])
            log.mount(CommandOutput(f"| Key | Value |\n|-----|-------|\n{rows}"))
        elif cmd == "/retry":
            if self._last_input:
                log.mount(CommandOutput("*Retrying last input\u2026*"))
                self._run_agent_turn(self._last_input)
            else:
                log.mount(CommandOutput(f"{ICON_WARN} No previous input to retry."))
        elif cmd == "/export":
            if self._agent is None:
                log.mount(CommandOutput(f"{ICON_WARN} No agent initialized."))
            else:
                save_path = f"conversation_{int(time.time())}.json"
                messages = self._agent.conversation.get_messages()
                try:
                    with open(save_path, "w", encoding="utf-8") as f:
                        json.dump(messages, f, ensure_ascii=False, indent=2)
                    log.mount(CommandOutput(f"Exported to `{save_path}`"))
                except Exception as e:
                    log.mount(CommandOutput(f"{ICON_ERROR} Export failed: {e}"))
        elif cmd.startswith("/copy"):
            if self._agent is None:
                log.mount(CommandOutput(f"{ICON_WARN} No agent initialized."))
                return True
            parts = command.strip().split(None, 1)
            target = parts[1].strip().lower() if len(parts) > 1 else "last"
            text, label = "", ""
            if target == "all" or target == "conversation":
                text, label = self._conversation_text(), "Copied conversation"
            elif target.startswith("#"):
                try:
                    index = int(target[1:]) - 1
                    messages = self._agent.conversation.get_messages()
                    text = self._format_content(messages[index].get("content", ""))
                    label = f"Copied message #{index + 1}"
                except Exception:
                    log.mount(CommandOutput(f"{ICON_WARN} Invalid message index: `{target}`"))
            else:
                text, label = self._last_answer_text(), "Copied last answer"
            if text:
                self._copy_worker(text, label)
                log.mount(CommandOutput(f"*Copying: {label}*"))
            else:
                log.mount(CommandOutput(f"{ICON_WARN} Nothing to copy."))
        elif cmd == "/compact":
            if self._agent is not None:
                self._agent.conversation.clear()
            log.mount(CommandOutput("*Context compacted.*"))
        elif cmd == "/debug":
            self._state.debug_mode = not self._state.debug_mode
            if self._agent is not None:
                status = await self._agent.get_status()
                rows = "\n".join(f"| {k} | {v} |" for k, v in status.items()
                                 if not isinstance(v, list))
                log.mount(CommandOutput(
                    f"Debug mode: **{'ON' if self._state.debug_mode else 'OFF'}**\n\n"
                    f"| Property | Value |\n|----------|-------|\n{rows}"
                ))
            else:
                log.mount(CommandOutput(
                    f"Debug mode: **{'ON' if self._state.debug_mode else 'OFF'}**"
                ))
        elif cmd.startswith("/theme"):
            parts = command.strip().split(None, 1)
            if len(parts) >= 2:
                name = parts[1].strip()
                if set_theme(name):
                    self._config.ui_theme = name
                    self.refresh_css()
                    log.mount(CommandOutput(f"Theme switched to **{name}**. "))
                else:
                    names = ", ".join(f"`{t}`" for t in AVAILABLE_THEMES)
                    log.mount(CommandOutput(f"{ICON_WARN} Unknown theme. Available: {names}"))
            else:
                from pc_assistant.ui.theme import get_theme_name
                current = get_theme_name()
                rows = "\n".join(
                    f"| {'**' + t + '**' if t == current else t} | {'active' if t == current else ''} |"
                    for t in AVAILABLE_THEMES
                )
                log.mount(CommandOutput(
                    f"Current: **{current}**\n\n"
                    f"| Theme | Status |\n|-------|--------|\n{rows}\n\n"
                    f"Use `/theme <name>` to switch."
                ))
        elif cmd == "/confirm":
            self._handle_confirm_reply(True, log)
        elif cmd == "/deny":
            self._handle_confirm_reply(False, log)
        else:
            log.mount(CommandOutput(f"{ICON_WARN} Unknown command: `{command}`"))

        log.scroll_end(animate=False)
        return True

    def _handle_confirm_reply(self, approved: bool, log: VerticalScroll) -> None:
        pending = getattr(self, "_confirm_pending", None)
        if pending is None:
            log.mount(CommandOutput(f"{ICON_WARN} No pending confirmation."))
            return
        ts = pending.get("ts")
        if ts is not None and time.time() - ts > CONFIRM_TIMEOUT:
            self._confirm_pending = None
            log.mount(CommandOutput(f"{ICON_WARN} Confirmation expired."))
            return
        self._confirm_pending = None
        code = pending["code"]
        tool = pending["tool"]
        status = "approved" if approved else "denied"
        log.mount(CommandOutput(f"**{tool}**: {status}"))

        future = pending.get("future")
        if future is not None and not future.done():
            future.set_result(approved)
            return

        from pc_assistant.service.client import ServiceClient
        if isinstance(self._agent, ServiceClient):
            asyncio.create_task(self._agent.confirm(code, approved))
