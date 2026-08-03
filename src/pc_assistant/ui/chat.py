"""Chat UI: backward-compatible entry point.

``ChatUI`` remains the public API used by ``pc_assistant.__init__.main()``.

- **TUI mode** (default): delegates to ``ChatApp`` (Textual).
- **Console mode** (headless/CI): ``_ConsoleView`` renders agent events through
  Rich, no Textual dependency needed at runtime.
"""
from __future__ import annotations

import asyncio
import json
import time
from io import StringIO
from typing import Any, Awaitable, Callable

from rich.console import Console
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table
from rich.text import Text as RichText

from pc_assistant.agent import Agent, AgentEvent
from pc_assistant.config import AppConfig
from pc_assistant.ui.state import UIState, MessageType
from pc_assistant.ui.theme import TOKYO_NIGHT

ICON_PROMPT = "\u25b8"       # ▸
ICON_ANSWER = "\u2502"       # │
ICON_TOOL = "\u25cf"         # ●
ICON_SUCCESS = "\u2713"      # ✓
ICON_ERROR = "\u2717"        # ✗
ICON_THINK = "\u25e6"        # ◦
ICON_WARN = "\u25b2"         # ▲
ICON_READY = "\u25cf"        # ●
ICON_CANCEL = "\u25a0"       # ■
ICON_BULLET = "\u2022"       # •

_COMMANDS_HELP = """\
/exit, /quit    Save conversation and exit
/clear          Clear conversation history
/memory         Show remembered user preferences
/memory clear   Clear all memories
/history        Show conversation history summary
/tools          List available tools
/status         Show detailed agent status
/help           Show this help message
/config         Show current configuration
/config set key=value   Set a config field at runtime
/retry          Retry the last user input
/debug          Toggle debug mode
/export         Export conversation to file
/compact        Compact context (remove old messages)\
"""


# ── Console-mode renderer (headless/CI) ───────────────────────────────


class _ConsoleView:
    """Console-mode renderer for agent events (headless/CI, no TUI).

    Renders through Rich; no Textual dependency needed.
    """

    def __init__(self, ui: ChatUI) -> None:
        self.ui = ui
        self.console = ui._console
        self.streaming_text = ""
        self.think_active = False
        self.answer_live: Live | None = None

    def render(self, event: AgentEvent) -> None:
        if event.type == "stream_start":
            self._stop_live()
            self.streaming_text = ""
            self.think_active = False
            self.console.print(RichText(f"{ICON_ANSWER} ", style="ai_label"))
            self.answer_live = Live(
                RichText(""),
                console=self.console,
                refresh_per_second=15,
                transient=False,
            )
            self.answer_live.start()

        elif event.type == "stream_delta":
            self.streaming_text += event.content
            if self.think_active:
                self.console.print()
                self.think_active = False
            if self.answer_live is not None:
                self.answer_live.update(RichText(self.streaming_text))

        elif event.type == "stream_think_delta":
            output = (
                self.answer_live.console if self.answer_live is not None else self.console
            )
            if not self.think_active:
                self.think_active = True
                output.print()
                output.print(RichText(f"{ICON_THINK} ", style="dim"), end="")
            output.print(RichText(event.content, style="dim"), end="")

        elif event.type == "stream_end":
            if self.answer_live is not None:
                if self.streaming_text:
                    self.answer_live.update(Markdown(self.streaming_text))
                self.answer_live.stop()
                self.answer_live = None
            self.think_active = False

        elif event.type == "tool_call":
            self._stop_live()
            if event.blocked:
                self.console.print(
                    RichText(f"{ICON_WARN} Blocked: {event.content}", style="warning")
                )
            else:
                items = list(event.tool_args.items())
                if items:
                    first_k, first_v = items[0]
                    val_str = json.dumps(first_v, ensure_ascii=False)
                    if len(val_str) > 60:
                        val_str = val_str[:57] + "..."
                    self.console.print(
                        RichText(
                            f"  {ICON_TOOL} {event.tool_name} {first_k}={val_str}",
                            style="tool_icon",
                        )
                    )
                else:
                    self.console.print(
                        RichText(f"  {ICON_TOOL} {event.tool_name}", style="tool_icon")
                    )

        elif event.type == "tool_result":
            result_str = (
                str(event.tool_result)
                if event.tool_result is not None
                else event.content
            )
            is_error = (
                isinstance(event.tool_result, dict)
                and "error" in event.tool_result
            )
            truncated = result_str[:200]
            if len(result_str) > 200:
                truncated += "..."
            icon = ICON_ERROR if is_error else ICON_SUCCESS
            style = "error" if is_error else "tool_result"
            self.console.print(RichText(f"    {icon} {truncated}", style=style))

        elif event.type == "final_answer":
            if not self.streaming_text and event.content:
                self.console.print(Markdown(event.content))

        elif event.type == "error":
            self._stop_live()
            self.console.print(RichText(f"{ICON_ERROR} {event.content}", style="error"))

        elif event.type == "iteration_limit":
            self._stop_live()
            self.console.print(
                RichText(f"{ICON_WARN} {event.content}", style="warning")
            )

        elif event.type == "cancelled":
            self._stop_live()
            self.console.print(
                RichText(f"{ICON_CANCEL} Cancelled.", style="warning")
            )

    def stop(self) -> None:
        self._stop_live()

    def _stop_live(self) -> None:
        if self.answer_live:
            self.answer_live.stop()
            self.answer_live = None
        if self.think_active:
            self.console.print()
            self.think_active = False


# ── ChatUI: backward-compatible public API ────────────────────────────


class ChatUI:
    """Public entry point for the chat interface.

    - ``run()`` launches the full-screen Textual TUI (``ChatApp``).
    - ``_process_events()`` / ``_show_welcome()`` still work in headless
      console mode for tests and CI.
    """

    def __init__(
        self,
        config: AppConfig,
        confirm_callback: Callable[[str, dict[str, Any]], bool | Awaitable[bool]] | None = None,
    ) -> None:
        self._config = config
        self._agent: Agent | None = None
        self._confirm_callback = confirm_callback
        self._running = False
        self._state = UIState()
        self._console = Console(theme=TOKYO_NIGHT)
        self._last_input: str = ""
        self._cancelled = False
        self._event_task: asyncio.Task | None = None
        self._tui_app: Any = None

    def set_agent(self, agent: Agent) -> None:
        self._agent = agent

    # ── TUI mode (Textual) ────────────────────────────────────────────

    async def run(self) -> None:
        """Start the full-screen Textual TUI."""
        from pc_assistant.ui.app import ChatApp

        self._running = True
        app = ChatApp(
            config=self._config,
            agent=self._agent,
            confirm_callback=self._confirm_callback,
        )
        self._tui_app = app
        await app.run_async()
        self._running = False

    # ── Console / headless mode ───────────────────────────────────────

    def _show_welcome(self) -> None:
        self._console.print("[bold cyan]PC Assistant[/bold cyan]")
        self._console.print("[dim]Type /help for commands.[/dim]")

    def _cancel(self) -> None:
        if self._cancelled:
            return
        self._cancelled = True
        if self._agent is not None:
            self._agent.cancel()

    async def _process_events(self, user_input: str) -> None:
        """Process a single user turn (headless console mode)."""
        if self._agent is None:
            self._state.add_message(MessageType.ERROR, "Agent not initialized.")
            self._console.print(
                RichText(f"{ICON_ERROR} Agent not initialized.", style="error")
            )
            return

        self._cancelled = False
        self._agent.reset_cancelled()
        self._state.processing = True

        console_view = _ConsoleView(self)

        try:
            async for event in self._agent.run(user_input):
                if self._cancelled:
                    console_view.render(AgentEvent(type="cancelled", content=""))
                    break
                self._handle_event(event)
                console_view.render(event)
        except asyncio.CancelledError:
            raise
        except KeyboardInterrupt:
            self._cancel()
        except Exception as e:
            self._console.print(RichText(f"{ICON_ERROR} {e}", style="error"))
        finally:
            console_view.stop()

        self._state.processing = False

    def _handle_event(self, event: AgentEvent) -> None:
        """Record an event into UIState (shared by both UI modes)."""
        if event.type == "stream_delta":
            pass
        elif event.type == "stream_think_delta":
            self._state.add_message(MessageType.THINK, event.content)
        elif event.type == "tool_call":
            self._state.add_message(
                MessageType.TOOL_CALL, f"[{event.tool_name}]",
                tool_name=event.tool_name, tool_args=event.tool_args,
            )
        elif event.type == "tool_result":
            result_str = (
                str(event.tool_result) if event.tool_result is not None else event.content
            )
            self._state.add_message(
                MessageType.TOOL_RESULT, result_str[:200],
                tool_name=event.tool_name,
            )
        elif event.type == "final_answer":
            self._state.add_message(MessageType.ASSISTANT, event.content)
        elif event.type == "error":
            self._state.add_message(MessageType.ERROR, event.content)

    # ── Slash commands (console mode) ─────────────────────────────────

    def _handle_user_command(self, command: str) -> bool:
        """Execute a slash command (console mode). Returns True for test compat."""
        cmd = command.lower().strip()

        if cmd in ("/exit", "/quit"):
            self._running = False
        elif cmd == "/clear":
            if self._agent is not None:
                self._agent.reset_conversation()
            self._state.clear_messages()
            self._console.print("[dim]Conversation history cleared.[/dim]")
        elif cmd == "/help":
            self._console.print(
                Panel(_COMMANDS_HELP, title="Commands", border_style="green", expand=False)
            )
        elif cmd == "/tools":
            if self._agent is None:
                self._console.print(f"[warning]{ICON_WARN} No agent initialized.[/warning]")
            else:
                tools = self._agent.registry.list_tools()
                if not tools:
                    self._console.print("[dim]No tools registered.[/dim]")
                else:
                    table = Table(title="Available Tools")
                    table.add_column("Tool", style="cyan bold")
                    for t in tools:
                        table.add_row(t)
                    self._console.print(table)
        elif cmd == "/history":
            if self._agent is None:
                self._console.print(f"[warning]{ICON_WARN} No agent initialized.[/warning]")
            else:
                messages = self._agent.conversation.get_messages()
                if not messages:
                    self._console.print("[dim]No conversation history.[/dim]")
                else:
                    table = Table(title="Conversation History", show_lines=True)
                    table.add_column("#", style="dim", width=4)
                    table.add_column("Role", style="bold", width=10)
                    table.add_column("Content", width=60)
                    for i, msg in enumerate(messages):
                        role = msg.get("role", "?")
                        content = msg.get("content", "")[:120]
                        table.add_row(str(i + 1), role, content)
                    self._console.print(table)
        elif cmd == "/config":
            table = Table(title="Configuration", show_lines=True)
            table.add_column("Key", style="bold")
            table.add_column("Value")
            table.add_row("Provider", self._config.llm_provider)
            table.add_row("Server", self._config.llm_server_url)
            table.add_row("Model", self._config.llm_model_name or "(not set)")
            table.add_row("API Key", self._config.masked_api_key())
            table.add_row("Max Iterations", str(self._config.max_iterations))
            table.add_row("Context Budget", str(self._config.context_window_budget))
            self._console.print(table)
        elif cmd == "/status":
            if self._agent is None:
                self._console.print(f"[warning]{ICON_WARN} No agent initialized.[/warning]")
            else:
                status = self._agent.get_status()
                table = Table(title="Agent Status", show_lines=True)
                table.add_column("Property", style="bold")
                table.add_column("Value")
                for k, v in status.items():
                    if isinstance(v, list):
                        v = ", ".join(str(x) for x in v)
                    table.add_row(k, str(v))
                self._console.print(table)
        elif cmd == "/debug":
            self._state.debug_mode = not self._state.debug_mode
            self._console.print(
                f"[dim]Debug mode: {'ON' if self._state.debug_mode else 'OFF'}[/dim]"
            )
        elif cmd.startswith("/config set "):
            parts = command.strip().split(None, 2)
            if len(parts) >= 3 and "=" in parts[2]:
                field_name, field_value = parts[2].split("=", 1)
                field_name = field_name.strip()
                field_value = field_value.strip()
                if self._config.set_field(field_name, field_value):
                    display = "****" if field_name == "llm_api_key" else field_value
                    self._console.print(f"[dim]Set {field_name} = {display}[/dim]")
                else:
                    self._console.print(
                        f"[warning]{ICON_WARN} Unknown config field: {field_name}[/warning]"
                    )
        elif cmd == "/memory clear":
            if self._agent is not None:
                self._agent.memory.clear()
            self._console.print("[dim]All memories cleared.[/dim]")
        elif cmd == "/memory":
            if self._agent is None:
                self._console.print(f"[warning]{ICON_WARN} No agent initialized.[/warning]")
            else:
                items = self._agent.memory.get_all()
                if not items:
                    self._console.print("[dim]No memories stored.[/dim]")
                else:
                    table = Table(title="User Memory", show_lines=True)
                    table.add_column("Category", style="bold", width=12)
                    table.add_column("Key", width=25)
                    table.add_column("Value", width=40)
                    for it in sorted(items, key=lambda x: x.category):
                        table.add_row(it.category, it.key, it.value[:60])
                    self._console.print(table)
        elif cmd == "/retry":
            if self._last_input:
                self._console.print("[dim]Retrying last input...[/dim]")
                self._event_task = asyncio.create_task(
                    self._process_events(self._last_input)
                )
            else:
                self._console.print(
                    f"[warning]{ICON_WARN} No previous input to retry.[/warning]"
                )
        elif cmd == "/export":
            if self._agent is None:
                self._console.print(
                    f"[warning]{ICON_WARN} No agent initialized.[/warning]"
                )
            else:
                save_path = f"conversation_{int(time.time())}.json"
                messages = self._agent.conversation.get_messages()
                try:
                    with open(save_path, "w", encoding="utf-8") as f:
                        json.dump(messages, f, ensure_ascii=False, indent=2)
                    self._console.print(f"[dim]Exported to: {save_path}[/dim]")
                except Exception as e:
                    self._console.print(
                        RichText(f"{ICON_ERROR} Export failed: {e}", style="error")
                    )
        elif cmd == "/compact":
            if self._agent is not None:
                self._agent.conversation.clear()
            self._console.print("[dim]Context compacted.[/dim]")
        else:
            self._console.print(
                f"[warning]{ICON_WARN} Unknown command: {command}[/warning]"
            )

        return True
