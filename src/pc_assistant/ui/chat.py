from __future__ import annotations

import asyncio
import json
import re
import signal
import sys
import threading
import time
from typing import Any, Callable

from pc_assistant.config import AppConfig
from pc_assistant.agent import Agent, AgentEvent

try:
    from rich.console import Console
    from rich.markdown import Markdown
    from rich.panel import Panel
    from rich.text import Text
    from rich.theme import Theme
    from rich.table import Table

    _HAS_RICH = True
except ImportError:
    _HAS_RICH = False

try:
    from prompt_toolkit import Application
    from prompt_toolkit.layout import HSplit, Window, Layout
    from prompt_toolkit.layout.controls import FormattedTextControl
    from prompt_toolkit.widgets import TextArea
    from prompt_toolkit.formatted_text import ANSI, StyleAndTextTuples
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.history import InMemoryHistory
    from prompt_toolkit.layout.screen import Point
    from prompt_toolkit.completion import WordCompleter, Completer

    _HAS_PT = True
except ImportError:
    _HAS_PT = False


if _HAS_PT:

    class _ChatWindow(Window):
        """Window that auto-scrolls to bottom when pin_to_bottom is True."""

        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            self.pin_to_bottom = True

        def _scroll(
            self, ui_content: UIContent, width: int, height: int
        ) -> None:
            if self.pin_to_bottom:
                if ui_content.line_count > 0:
                    ui_content.cursor_position = Point(x=0, y=ui_content.line_count - 1)
                super()._scroll(ui_content, width, height)

        def _scroll_up(self) -> None:
            self.pin_to_bottom = False
            super()._scroll_up()

        def _scroll_down(self) -> None:
            super()._scroll_down()
            info = self.render_info
            if info is not None and info.vertical_scroll >= info.content_height - info.window_height - 1:
                self.pin_to_bottom = True


_WELCOME_ART = r"""
  ____  _        _   _               ____           _
 |  _ \(_)      | | | |             |  _ \         | |
 | |_) |_  ___  | |_| |__   ___ _ __| |_) | ___  __| |
 |  _ <| |/ _ \ | __| '_ \ / _ \ '__|  _ < / _ \/ _` |
 | |_) | |  __/ | |_| | | |  __/ |  | |_) |  __/ (_| |
 |____/|_|\___|  \__|_| |_|\___|_|  |____/ \___|\__,_|
"""

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
/screenshot     Take a screenshot
/retry          Retry the last user input
/debug          Show debug information
/export         Export conversation to file
/compact        Compact context (remove old messages)\
"""

# Command autocompletions for Tab key
_COMMAND_COMPLETIONS = [
    "/exit", "/quit", "/clear", "/memory", "/memory clear",
    "/history", "/tools", "/status", "/help", "/config",
    "/config set ", "/screenshot", "/retry", "/debug", "/export", "/compact",
]


class _CommandCompleter(Completer):
    """Custom completer that only completes commands starting with '/'."""
    def __init__(self, commands: list[str]) -> None:
        self._commands = commands

    def get_completions(self, document, complete_event):
        text = document.text
        if not text.startswith("/"):
            return
        for cmd in self._commands:
            if cmd.startswith(text.lower()):
                from prompt_toolkit.completion import Completion
                yield Completion(cmd, start_position=-len(text))


class _ChatStream:
    """Captures Rich ANSI output and feeds it into the prompt_toolkit display."""
    def __init__(self, target: list[str], flush_cb: Callable[[], None]) -> None:
        self._target = target
        self._flush_cb = flush_cb
        self._buf: list[str] = []

    def write(self, text: str) -> None:
        self._buf.append(text)

    def flush(self) -> None:
        if self._buf:
            # Rich output is already ANSI-formatted, store as-is
            self._target.append("".join(self._buf))
            self._buf.clear()
            self._flush_cb()


class ChatUI:
    def __init__(
        self,
        config: AppConfig,
        confirm_callback: Callable[[str, str], bool] | None = None,
    ) -> None:
        self._config = config
        self._agent: Agent | None = None
        self._confirm_callback = confirm_callback
        self._running = False
        self._cancelled = False
        self._processing = False
        self._think_content = ""
        self._current_op = ""
        self._spinner_idx = 0

        self._all_chat_parts: list[str] = []
        self._input_field: TextArea | None = None
        self._chat_window: Window | None = None
        self._pt_app: Application | None = None
        self._last_input: str = ""
        self._last_screenshot_path: str = "screenshot.png"

        # Password input handling
        self._password_input_event: asyncio.Event | None = None
        self._pending_password: str = ""
        self._password_mode: bool = False

        if _HAS_RICH:
            custom_theme = Theme({
                "thought": "dim italic",
                "tool_name": "cyan bold",
                "tool_args": "cyan",
                "tool_result": "dim",
                "final_answer": "bold",
                "error": "red bold",
                "warning": "yellow",
                "prompt": "green bold",
                "ai_label": "blue bold",
                "think_label": "dim italic",
                "status_bar": "dim",
            })
            self._chat_stream = _ChatStream(self._all_chat_parts, self._flush_display)
            self._pt_console = Console(
                file=self._chat_stream,
                force_terminal=True,
                color_system="truecolor",
                theme=custom_theme,
            )
            self._real_console = Console(theme=custom_theme)
            self._console = self._pt_console
        else:
            self._console = None

    # ── display helpers ──────────────────────────────────────────────

    def _flush_display(self) -> None:
        if self._pt_app and self._pt_app.is_running:
            self._pt_app.invalidate()

    def _write_raw(self, text: str) -> None:
        self._all_chat_parts.append(text)
        self._flush_display()

    def _print(self, *args: Any, **kwargs: Any) -> None:
        if self._console is not None:
            self._console.print(*args, **kwargs)
        else:
            plain = " ".join(str(a) for a in args)
            print(plain)

    def _print_tool_call(self, name: str, arguments: dict[str, Any]) -> None:
        # ANSI colors
        CYAN = "\033[36m"
        DIM = "\033[2m"
        RESET = "\033[0m"

        if self._console is not None:
            items = list(arguments.items())
            if len(items) <= 2:
                summary_parts = [f"{k}={json.dumps(v, ensure_ascii=False)}" for k, v in items]
                summary = ", ".join(summary_parts)
                self._console.print(
                    Text(f"  {CYAN}[{name}]{RESET} {DIM}{summary}{RESET}")
                )
            else:
                summary_parts = [f"{k}={json.dumps(v, ensure_ascii=False)}" for k, v in items[:2]]
                summary = ", ".join(summary_parts)
                remaining = len(items) - 2
                self._console.print(
                    Text(f"  {CYAN}[{name}]{RESET} {DIM}{summary}{RESET}")
                )
                self._console.print(
                    Text(f"    {DIM}({remaining} more params){RESET}", style="dim")
                )
        else:
            print(f"  [tool: {name}] {json.dumps(arguments, ensure_ascii=False)}")

    def _print_tool_result(self, name: str, result: str, is_error: bool = False) -> None:
        GREEN = "\033[32m"
        RED = "\033[31m"
        DIM = "\033[2m"
        RESET = "\033[0m"

        if self._console is not None:
            truncated = result[:500]
            if len(result) > 500:
                truncated += f"... ({len(result)} chars total)"
            if is_error:
                self._console.print(
                    Text(f"    {RED}✗{RESET} {DIM}{truncated}{RESET}")
                )
            else:
                self._console.print(
                    Text(f"    {GREEN}✓{RESET} {DIM}{truncated}{RESET}")
                )
        else:
            print(f"  ← {name}: {result[:200]}")

    def _print_error(self, message: str) -> None:
        RED = "\033[31m"
        RESET = "\033[0m"
        if self._console is not None:
            self._console.print(f"{RED}✗ {message}{RESET}")
        else:
            print(f"ERROR: {message}")

    def _print_warning(self, message: str) -> None:
        YELLOW = "\033[33m"
        RESET = "\033[0m"
        if self._console is not None:
            self._console.print(f"{YELLOW}! {message}{RESET}")
        else:
            print(f"WARNING: {message}")

    # ── new command handlers ──────────────────────────────────────────

    def _handle_screenshot(self) -> None:
        """Take a screenshot and save to file."""
        import time
        save_path = f"screenshot_{int(time.time())}.png"
        try:
            import mss
            from PIL import Image
            with mss.mss() as sct:
                monitor = sct.monitors[0]
                shot = sct.grab(monitor)
                img = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
                img.save(save_path)
            self._print(f"[dim]Screenshot saved to: {save_path}[/dim]" if self._console else f"Screenshot saved to: {save_path}")
        except ImportError as e:
            self._print_error(f"Missing dependency: {e}")
        except Exception as e:
            self._print_error(f"Failed to take screenshot: {e}")

    def _handle_debug(self) -> None:
        """Show debug information."""
        if self._agent is None:
            self._print_warning("No agent initialized yet.")
            return
        status = self._agent.get_status()
        if self._console is not None:
            table = Table(title="Debug Information", show_lines=True)
            table.add_column("Property", style="bold")
            table.add_column("Value")
            for k, v in status.items():
                if isinstance(v, list):
                    v = ", ".join(str(x) for x in v[:10])
                    if len(str(v)) > 100:
                        v = str(v)[:100] + "..."
                table.add_row(k, str(v))
            self._console.print(table)
        else:
            for k, v in status.items():
                print(f"  {k}: {v}")

    def _handle_export(self) -> None:
        """Export conversation to file."""
        import time
        save_path = f"conversation_{int(time.time())}.json"
        if self._agent is None:
            self._print_warning("No agent initialized yet.")
            return
        messages = self._agent.conversation.get_messages()
        import json
        try:
            with open(save_path, "w", encoding="utf-8") as f:
                json.dump(messages, f, ensure_ascii=False, indent=2)
            self._print(f"[dim]Conversation exported to: {save_path}[/dim]" if self._console else f"Conversation exported to: {save_path}")
        except Exception as e:
            self._print_error(f"Failed to export: {e}")

    def _show_welcome(self) -> None:
        if self._console is not None:
            if _HAS_PT and self._pt_app is not None:
                self._all_chat_parts.append(f"\x1b[1m\x1b[32m{_WELCOME_ART}\x1b[0m\n")
                from pc_assistant import __version__
                self._all_chat_parts.append(f"  \x1b[1mv{__version__}\x1b[0m  •  Type \x1b[1m/help\x1b[0m for commands\n\n")
            else:
                self._console.print(_WELCOME_ART, style="bold green", highlight=False)
                from pc_assistant import __version__
                self._console.print(f"  [bold]v{__version__}[/bold]  •  Type [bold]/help[/bold] for commands\n")
        else:
            from pc_assistant import __version__
            self._all_chat_parts.append(f"PC Assistant v{__version__}\nType /help for commands\n\n")

    # ── status bar ───────────────────────────────────────────────────

    def _get_status_fragments(self) -> StyleAndTextTuples:
        if self._agent is None:
            return [("", " initializing...")]

        status = self._agent.get_status()
        provider = status.get("provider", "unknown")
        model = status.get("model", "default")
        connected = status.get("connected", False)
        agent_status = status.get("status", "ready")
        turns = status.get("conversation_turns", 0)
        total_tokens = status.get("total_tokens", 0)
        memory_items = status.get("memory_items", 0)
        iterations = status.get("total_iterations", 0)
        prompt_tokens = status.get("total_prompt_tokens", 0)
        completion_tokens = status.get("total_completion_tokens", 0)

        # Format token count
        if total_tokens >= 1_000_000:
            token_str = f"{total_tokens / 1_000_000:.1f}M"
        elif total_tokens >= 1000:
            token_str = f"{total_tokens / 1000:.1f}k"
        else:
            token_str = str(total_tokens)

        # Use prompt_toolkit style names (not ANSI escape sequences in tuple values)
        # Status colors and symbols
        status_styles = {
            "ready": ("#00ff00", "●"),      # bright green
            "thinking": ("#00aaff", "◐"),   # bright blue
            "executing": ("#ffaa00", "◑"),  # orange
        }
        style_color, style_icon = status_styles.get(agent_status, ("#888888", "○"))

        # Connection status
        conn_color = "#00ff00" if connected else "#ff4444"  # bright green or red
        conn_icon = "●"

        frags: StyleAndTextTuples = [
            ("", " "),
            (f"bold {conn_color}", conn_icon),
            ("", f" {provider} | {model} | "),
        ]

        if self._processing:
            _spin_frames = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
            sp = _spin_frames[self._spinner_idx % len(_spin_frames)]
            op = f" {self._current_op}" if self._current_op else ""
            frags.append((f"bold {style_color}", f"{sp} {agent_status}{op}"))
        else:
            frags.append((style_color, f"{style_icon} {agent_status}"))

        frags.append(("", f" | {turns} turns"))
        frags.append(("", f" | {memory_items} mem"))
        frags.append(("", f" | {iterations} iter"))
        frags.append(("", f" | {prompt_tokens} in"))
        frags.append(("", f" | {completion_tokens} out"))
        frags.append(("", f" | {token_str} tokens"))
        return frags

    # ── commands ─────────────────────────────────────────────────────

    def _handle_user_command(self, command: str) -> bool:
        cmd = command.lower().strip()

        if cmd in ("/exit", "/quit"):
            self._print("[dim]Goodbye![/dim]" if self._console else "Goodbye!")
            self._running = False
            return True

        if cmd == "/clear":
            if self._agent is not None:
                self._agent.reset_conversation()
            self._print("[dim]Conversation history cleared.[/dim]" if self._console else "Conversation history cleared.")
            return True

        if cmd == "/history":
            if self._agent is None:
                self._print_warning("No agent initialized yet.")
                return True
            messages = self._agent.conversation.get_messages()
            if not messages:
                self._print("[dim]No conversation history.[/dim]" if self._console else "No conversation history.")
                return True
            if self._console is not None:
                table = Table(title="Conversation History", show_lines=True)
                table.add_column("#", style="dim", width=4)
                table.add_column("Role", style="bold", width=10)
                table.add_column("Content", width=60)
                for i, msg in enumerate(messages):
                    role = msg.get("role", "?")
                    content = msg.get("content", "")
                    if len(content) > 120:
                        content = content[:117] + "..."
                    table.add_row(str(i + 1), role, content)
                self._console.print(table)
            else:
                for i, msg in enumerate(messages):
                    role = msg.get("role", "?")
                    content = msg.get("content", "")[:80]
                    print(f"  {i + 1}. [{role}] {content}")
            return True

        if cmd == "/tools":
            if self._agent is None:
                self._print_warning("No agent initialized yet.")
                return True
            tools = self._agent.registry.list_tools()
            if not tools:
                self._print("[dim]No tools registered.[/dim]" if self._console else "No tools registered.")
                return True
            if self._console is not None:
                table = Table(title="Available Tools")
                table.add_column("Tool", style="cyan bold")
                for t in tools:
                    table.add_row(t)
                self._console.print(table)
            else:
                print("Available tools:")
                for t in tools:
                    print(f"  - {t}")
            return True

        if cmd == "/help":
            if self._console is not None:
                self._console.print(
                    Panel(
                        _COMMANDS_HELP,
                        title="Commands",
                        border_style="green",
                        expand=False,
                    )
                )
            else:
                print("Commands:")
                print(_COMMANDS_HELP)
            return True

        if cmd == "/config":
            parts = command.strip().split(None, 2)
            if len(parts) >= 3 and parts[1].lower() == "set":
                field_name = parts[2].split("=", 1)[0].strip() if "=" in parts[2] else ""
                field_value = parts[2].split("=", 1)[1].strip() if "=" in parts[2] else ""
                if not field_name or not field_value:
                    self._print_warning("Usage: /config set key=value")
                    return True
                if self._config.set_field(field_name, field_value):
                    display_val = "****" if field_name == "llm_api_key" else field_value
                    self._print(f"[dim]Set {field_name} = {display_val}[/dim]" if self._console else f"Set {field_name}")
                else:
                    self._print_warning(f"Unknown or invalid config field: {field_name}")
                return True
            if self._console is not None:
                table = Table(title="Configuration", show_lines=True)
                table.add_column("Key", style="bold")
                table.add_column("Value")
                table.add_row("Provider", self._config.llm_provider)
                table.add_row("LLM Server", self._config.llm_server_url)
                table.add_row("Model", self._config.llm_model_name or "(not set)")
                table.add_row("API Key", self._config.masked_api_key())
                table.add_row("Max Iterations", str(self._config.max_iterations))
                table.add_row("Shell Timeout", str(self._config.shell_timeout))
                table.add_row("Context Budget", str(self._config.context_window_budget))
                table.add_row("Log File", self._config.log_file)
                table.add_row("Working Dir", self._config.working_directory)
                self._console.print(table)
            else:
                print(f"  Provider:    {self._config.llm_provider}")
                print(f"  LLM Server:  {self._config.llm_server_url}")
                print(f"  Model:       {self._config.llm_model_name or '(not set)'}")
                print(f"  API Key:     {self._config.masked_api_key()}")
                print(f"  Max Iters:   {self._config.max_iterations}")
                print(f"  Working Dir: {self._config.working_directory}")
            return True

        if cmd == "/status":
            if self._agent is None:
                self._print_warning("No agent initialized yet.")
                return True
            status = self._agent.get_status()
            if self._console is not None:
                table = Table(title="Agent Status", show_lines=True)
                table.add_column("Property", style="bold")
                table.add_column("Value")
                # Use Rich markup for table rows
                GREEN = "\033[32m"
                RED = "\033[31m"
                RESET = "\033[0m"
                conn_status = f"{GREEN}●{RESET} Yes" if status["connected"] else f"{RED}●{RESET} No"
                table.add_row("Provider", status["provider"])
                table.add_row("Model", status["model"])
                table.add_row("Connected", conn_status)
                table.add_row("Status", status["status"])
                table.add_row("Platform", status["platform"])
                table.add_row("Working Dir", status["working_directory"])
                table.add_row("Conversation Turns", str(status["conversation_turns"]))
                table.add_row("Total Iterations", str(status["total_iterations"]))
                table.add_row("Prompt Tokens", str(status["total_prompt_tokens"]))
                table.add_row("Completion Tokens", str(status["total_completion_tokens"]))
                table.add_row("Total Tokens", str(status["total_tokens"]))
                table.add_row("Memory Items", str(status["memory_items"]))
                table.add_row("Tools", ", ".join(status["tools"]))
                self._console.print(table)
            else:
                for k, v in status.items():
                    print(f"  {k}: {v}")
            return True

        if cmd == "/memory clear":
            if self._agent is None:
                self._print_warning("No agent initialized yet.")
                return True
            self._agent.memory.clear()
            self._print("[dim]All memories cleared.[/dim]" if self._console else "All memories cleared.")
            return True

        if cmd == "/memory":
            if self._agent is None:
                self._print_warning("No agent initialized yet.")
                return True
            items = self._agent.memory.get_all()
            if not items:
                self._print("[dim]No memories stored yet. I'll learn your preferences as we chat![/dim]" if self._console else "No memories stored yet.")
                return True
            if self._console is not None:
                table = Table(title="User Memory", show_lines=True)
                table.add_column("Category", style="bold", width=12)
                table.add_column("Key", width=25)
                table.add_column("Value", width=40)
                table.add_column("Access", width=6)
                for item in sorted(items, key=lambda x: x.category):
                    table.add_row(item.category, item.key, item.value[:60], str(item.access_count))
                self._console.print(table)
            else:
                for item in items:
                    print(f"  [{item.category}] {item.key}: {item.value}")
            return True

        # New commands
        if cmd == "/screenshot":
            self._handle_screenshot()
            return True

        if cmd == "/retry":
            if hasattr(self, '_last_input') and self._last_input:
                self._print("[dim]Retrying last input...[/dim]" if self._console else "Retrying last input...")
                asyncio.create_task(self._process_events(self._last_input))
            else:
                self._print_warning("No previous input to retry.")
            return True

        if cmd == "/debug":
            self._handle_debug()
            return True

        if cmd == "/export":
            self._handle_export()
            return True

        if cmd == "/compact":
            if self._agent is not None:
                self._agent.conversation.clear()
                self._print("[dim]Context compacted (conversation cleared).[/dim]" if self._console else "Context compacted.")
            return True

        self._print_warning(f"Unknown command: {command}")
        return True

    # ── agent event loop ─────────────────────────────────────────────

    async def _process_events(self, user_input: str) -> None:
        if self._agent is None:
            self._print_error("Agent not initialized.")
            return

        self._cancelled = False
        self._agent._cancelled = False

        think_start_time: float | None = None
        first_content_received = False
        stream_content_parts: list[str] = []

        def _cancel_handler() -> None:
            self._cancelled = True
            if self._agent is not None:
                self._agent.cancel()
            # Write directly to _all_chat_parts to preserve ANSI codes
            self._all_chat_parts.append("\n\033[33m! Cancelling request...\033[0m\n")
            self._flush_display()

        loop = asyncio.get_event_loop()
        try:
            loop.add_signal_handler(signal.SIGINT, _cancel_handler)
        except (NotImplementedError, OSError):
            pass

        try:
            async for event in self._agent.run(user_input):
                if self._cancelled:
                    self._all_chat_parts.append("\n\033[33m! Operation cancelled.\033[0m\n")
                    self._flush_display()
                    break

                if event.type == "stream_start":
                    first_content_received = False
                    stream_content_parts = []
                    self._current_op = "Waiting..."

                elif event.type == "think_start":
                    think_start_time = time.time()
                    self._think_content = ""
                    self._current_op = "Thinking..."
                    if self._console is not None:
                        self._console.print()
                        self._console.print("[think_label]  → Thinking...[/think_label]")
                        self._write_raw("\033[2m\033[3m")
                    else:
                        self._all_chat_parts.append("\n  → Thinking...\n")
                        self._flush_display()

                elif event.type == "stream_think_delta":
                    self._think_content += event.content
                    self._write_raw(event.content)

                elif event.type == "think_end":
                    self._write_raw("\033[0m")
                    self._current_op = ""
                    elapsed = time.time() - think_start_time if think_start_time else 0
                    if self._console is not None:
                        self._console.print()
                        self._console.print(f"[think_label]  ← {elapsed:.1f}s[/think_label]")
                    else:
                        self._all_chat_parts.append(f"\n  ← {elapsed:.1f}s\n")
                        self._flush_display()
                    self._think_content = ""
                    think_start_time = None

                elif event.type == "stream_delta":
                    if not first_content_received:
                        first_content_received = True
                        # Write directly to _all_chat_parts to preserve ANSI codes
                        self._all_chat_parts.append("\n" + "─" * 40 + "\n")
                        # Bright cyan bold AI label
                        self._all_chat_parts.append("\x1b[1m\x1b[96mAI>\x1b[0m ")
                        self._flush_display()
                    stream_content_parts.append(event.content)
                    self._write_raw(event.content)

                elif event.type == "stream_end":
                    if first_content_received:
                        if self._console is not None:
                            self._console.print()
                        else:
                            self._all_chat_parts.append("\n")
                            self._flush_display()

                elif event.type == "thought":
                    pass

                elif event.type == "tool_call":
                    self._current_op = f"Running {event.tool_name}..."
                    if event.blocked:
                        self._print_warning(f"Blocked: {event.content}")
                    else:
                        self._print_tool_call(event.tool_name, event.tool_args)

                elif event.type == "tool_result":
                    self._current_op = ""
                    result_str = str(event.tool_result) if event.tool_result is not None else event.content
                    is_error = isinstance(event.tool_result, dict) and "error" in event.tool_result
                    self._print_tool_result(event.tool_name, result_str, is_error)

                elif event.type == "final_answer":
                    self._current_op = ""
                    if not first_content_received and event.content:
                        # Write directly to _all_chat_parts to preserve ANSI codes
                        self._all_chat_parts.append("\n")
                        # Bright cyan bold AI label
                        self._all_chat_parts.append("\x1b[1m\x1b[96mAI>\x1b[0m ")
                        self._all_chat_parts.append(event.content + "\n")
                        self._flush_display()

                elif event.type == "error":
                    self._print_error(event.content)

                elif event.type == "iteration_limit":
                    self._print_warning(event.content)

                elif event.type == "cancelled":
                    self._print_warning("Operation cancelled by user.")

        except KeyboardInterrupt:
            _cancel_handler()
            self._print_warning("Operation interrupted.")
        finally:
            try:
                loop.remove_signal_handler(signal.SIGINT)
            except (NotImplementedError, OSError):
                pass
            self._current_op = ""

    # ── prompt_toolkit layout ────────────────────────────────────────

    def _get_chat_ansi(self) -> ANSI:
        # _all_chat_parts contains mixed content:
        # - Rich Console output (already ANSI-formatted)
        # - Direct ANSI strings from _write_raw
        # Just join and let ANSI parser handle it
        if not self._all_chat_parts:
            return ANSI("")
        combined = "".join(self._all_chat_parts)
        return ANSI(combined)

    def _build_pt_layout(self) -> Layout:
        kb = KeyBindings()

        @kb.add("enter")
        def _enter(event: Any) -> None:
            if self._input_field is None:
                return
            text = self._input_field.text.strip()
            if text and not self._processing:
                self._input_field.text = ""
                asyncio.create_task(self._handle_user_input(text))

        @kb.add("c-c")
        def _ctrl_c(event: Any) -> None:
            if self._processing:
                self._cancelled = True
                if self._agent is not None:
                    self._agent.cancel()
                # Write directly to preserve ANSI codes
                self._all_chat_parts.append("\n\033[33m! Cancelling request...\033[0m\n")
                self._flush_display()
            else:
                self._running = False
                event.app.exit()

        @kb.add("c-d")
        def _ctrl_d(event: Any) -> None:
            self._running = False
            event.app.exit()

        @kb.add("pageup")
        def _page_up(event: Any) -> None:
            if self._chat_window is not None:
                self._chat_window._scroll_up()

        @kb.add("pagedown")
        def _page_down(event: Any) -> None:
            if self._chat_window is not None:
                self._chat_window._scroll_down()

        @kb.add("end")
        def _end(event: Any) -> None:
            if self._chat_window is not None:
                self._chat_window.pin_to_bottom = True
            self._flush_display()

        chat_control = FormattedTextControl(self._get_chat_ansi)
        self._chat_window = _ChatWindow(content=chat_control, wrap_lines=True)

        # Create command completer for Tab auto-completion
        command_completer = _CommandCompleter(_COMMAND_COMPLETIONS)

        self._input_field = TextArea(
            height=1,
            multiline=False,
            prompt="You> ",
            history=InMemoryHistory(),
            completer=command_completer,
        )

        status_control = FormattedTextControl(self._get_status_fragments)
        status_window = Window(
            content=status_control,
            height=1,
            dont_extend_height=True,
            style="bg:#1a1a2e",
        )

        root = HSplit([
            self._chat_window,
            self._input_field,
            status_window,
        ])

        layout = Layout(root)
        self._pt_app = Application(
            layout=layout,
            key_bindings=kb,
            full_screen=True,
            mouse_support=True,
        )

        self._pt_app.create_background_task(self._refresh_loop())
        return layout

    async def _refresh_loop(self) -> None:
        await asyncio.sleep(0.05)
        self._flush_display()
        while self._running:
            await asyncio.sleep(0.08)
            self._spinner_idx += 1
            self._flush_display()

    async def ask_input(self, prompt: str, password_mode: bool = False) -> str | None:
        """Request interactive input from user (e.g., for sudo password).

        Displays prompt and waits for input in the main chat input area.

        Args:
            prompt: The prompt message to display
            password_mode: If True, input should be hidden (but displayed as dots)

        Returns:
            User input string, or None if cancelled
        """
        # Display prompt in chat
        self._all_chat_parts.append(f"\n\x1b[33m! {prompt}\x1b[0m\n")
        if password_mode:
            self._all_chat_parts.append(f"\x1b[36mType password and press Enter (input will be hidden): \x1b[0m")
        else:
            self._all_chat_parts.append(f"\x1b[36mType your input and press Enter: \x1b[0m")
        self._flush_display()

        # Create an event to signal when input is ready
        self._password_input_event = asyncio.Event()
        self._pending_password = ""
        self._password_mode = password_mode

        try:
            # Wait for user input (they type in the main input area)
            await asyncio.wait_for(
                self._password_input_event.wait(),
                timeout=120  # 2 minute timeout
            )
            return self._pending_password
        except asyncio.TimeoutError:
            return None
        except asyncio.CancelledError:
            return None
        finally:
            self._password_input_event = None
            self._pending_password = ""
            self._password_mode = False

    async def _handle_user_input(self, text: str) -> None:
        self._processing = True
        if self._chat_window is not None:
            self._chat_window.pin_to_bottom = True

        # Handle password input if pending
        if hasattr(self, '_password_input_event') and self._password_input_event is not None:
            self._pending_password = text
            # Display as dots if in password mode
            if self._password_mode:
                self._all_chat_parts.append(f"\x1b[32mPassword: \x1b[0m{'*' * len(text)}\n")
            else:
                self._all_chat_parts.append(f"\x1b[32mInput: \x1b[0m{text}\n")
            self._password_input_event.set()
            self._processing = False
            self._flush_display()
            return

        # Save non-command input for /retry
        if not text.startswith("/"):
            self._last_input = text

        # User input with green color
        self._all_chat_parts.append(f"\x1b[32mYou> \x1b[0m{text}\n")
        self._flush_display()

        try:
            if text.startswith("/"):
                handled = self._handle_user_command(text)
                _ = handled
                if not self._running and self._pt_app is not None:
                    self._pt_app.exit()
                    return
            else:
                await self._process_events(text)
        finally:
            self._processing = False
            if self._input_field is not None:
                self._input_field.read_only = False
            self._flush_display()

    # ── fallback (no prompt_toolkit) ──────────────────────────────────

    async def _run_fallback(self) -> None:
        try:
            from rich.prompt import Prompt
            _has_prompt = True
        except ImportError:
            _has_prompt = False

        self._console = self._real_console
        self._running = True
        self._show_welcome()

        while self._running:
            try:
                if _has_prompt and self._console is not None:
                    user_input = Prompt.ask("[prompt]You>[/prompt]", console=self._console)
                else:
                    user_input = input("You> ").strip()
            except (EOFError, KeyboardInterrupt):
                self._print("\n[dim]Goodbye![/dim]" if self._console else "\nGoodbye!")
                break

            if user_input is None:
                break
            user_input = user_input.strip()
            if not user_input:
                continue

            if user_input.startswith("/"):
                self._handle_user_command(user_input)
                continue

            await self._process_events(user_input)

    # ── main entry ────────────────────────────────────────────────────

    async def run(self) -> None:
        if not _HAS_PT or not _HAS_RICH:
            await self._run_fallback()
            return

        self._running = True
        self._build_pt_layout()
        self._show_welcome()

        await self._pt_app.run_async()
