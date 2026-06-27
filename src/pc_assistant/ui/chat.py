from __future__ import annotations

import asyncio
import json
import re
import shutil
import signal
import time
from io import StringIO
from typing import Any, Awaitable, Callable

from pc_assistant.config import AppConfig
from pc_assistant.agent import Agent
from pc_assistant.ui.state import UIState, MessageType
from pc_assistant.ui.theme import TOKYO_NIGHT

from rich.console import Console
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table
from rich.text import Text as RichText

from prompt_toolkit.application import Application
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.document import Document
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import Layout, HSplit, Window, WindowAlign
from prompt_toolkit.layout.controls import BufferControl, FormattedTextControl
from prompt_toolkit.lexers import Lexer
from prompt_toolkit.styles import Style as PTStyle

_WELCOME_ART = r"""
  ____  _        _   _               ____           _
 |  _ \(_)      | | | |             |  _ \         | |
 | |_) |_  ___  | |_| |__   ___ _ __| |_) | ___  __| |
 |  _ <| |/ _ \ | __| '_ \ / _ \ '__|  _ < / _ \/ _` |
 | |_) | |  __/ | |_| | | |  __/ |  | |_) |  __/ (_| |
 |____/|_|\___|  \__|_| |_|\___|_|  |____/ \___|\__,_|
"""

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
/screenshot     Take a screenshot
/retry          Retry the last user input
/debug          Toggle debug mode
/export         Export conversation to file
/compact        Compact context (remove old messages)\
"""

TUI_STYLE = PTStyle.from_dict({
    "status": "bg:#1a1b26 fg:#565f89",
    "status.ready": "bold fg:#9ece6a",
    "status.thinking": "bold fg:#7aa2f7",
    "status.executing": "bold fg:#e0af68",
    "status.info": "italic fg:#3b4261",
    "status.hint": "fg:#3b4261",
})

_ANSI_RX = re.compile(r'\x1b\[[0-9;]*m')


def _strip_ansi(text: str) -> str:
    return _ANSI_RX.sub('', text)


def _render_plain(text: str, width: int = 80, markdown: bool = True) -> str:
    buf = StringIO()
    console = Console(
        theme=TOKYO_NIGHT,
        file=buf,
        force_terminal=False,
        no_color=True,
        width=max(40, min(width, shutil.get_terminal_size().columns - 4)),
    )
    if markdown:
        console.print(Markdown(text))
    else:
        console.print(text)
    return _strip_ansi(buf.getvalue())


class _ChatLexer(Lexer):
    """Color chat output based on line prefix characters."""
    def lex_document(self, document: Document):
        lines = document.lines

        def get_line(lineno: int):
            line = lines[lineno] if lineno < len(lines) else ""
            stripped = line.lstrip()
            if stripped.startswith(ICON_ANSWER):
                return [("#7aa2f7", line)]
            if stripped.startswith(ICON_THINK):
                return [("#3b4261", line)]
            if stripped.startswith(ICON_TOOL):
                return [("#73daca", line)]
            if stripped.startswith(ICON_SUCCESS):
                return [("#9ece6a", line)]
            if stripped.startswith(ICON_ERROR):
                return [("#f7768e", line)]
            if stripped.startswith(ICON_WARN):
                return [("#e0af68", line)]
            if stripped.startswith(ICON_CANCEL):
                return [("#f7768e", line)]
            if stripped.startswith(ICON_PROMPT):
                return [("#9ece6a", line)]
            if "____" in line or "|_" in line:
                return [("#7aa2f7", line)]
            return [("", line)]

        return get_line


class ChatUI:
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

        self._app: Application | None = None
        self._chat_buffer: Buffer | None = None
        self._input_buffer: str = ""
        self._input_cursor: int = 0
        self._kb: KeyBindings | None = None
        self._refresh_task: asyncio.Task | None = None
        self._spinner_idx = 0

        self._chat_text: str = ""
        self._stream_start: int = 0
        self._stream_text: str = ""
        self._think_start: int = 0
        self._think_text: str = ""
        self._think_active: bool = False
        self._stream_rendered: bool = False
        self._current_op = ""
        self._token_count = 0
        self._last_assistant_text: str = ""
        self._confirm_future: asyncio.Future | None = None

    def set_agent(self, agent: Agent) -> None:
        self._agent = agent
        agent._confirm_callback = self._tui_confirm

    async def _tui_confirm(self, tool_name: str, arguments: dict[str, Any]) -> bool:
        details = "\n".join(
            f"  {k}: {json.dumps(v, ensure_ascii=False)}"
            for k, v in list(arguments.items())[:4]
        )
        self._append_text(f"\n{ICON_WARN} Confirm: {tool_name}\n{details}\nProceed? (y/n): ")
        self._rebuild_buffer()
        loop = asyncio.get_event_loop()
        self._confirm_future = loop.create_future()
        try:
            return await self._confirm_future
        finally:
            self._confirm_future = None

    # ── Text management ────────────────────────────────────────────────

    def _append_text(self, text: str) -> None:
        self._chat_text += text

    def _replace_text_range(self, start: int, text: str) -> None:
        self._chat_text = self._chat_text[:start] + text

    def _rebuild_buffer(self) -> None:
        if self._chat_buffer is None:
            return
        text = self._chat_text
        if not self._state.processing:
            text += f"\n  {ICON_PROMPT} {self._input_buffer}"
        self._chat_buffer.set_document(
            Document(text, cursor_position=len(text)),
            bypass_readonly=True,
        )

    # ── Console helpers ────────────────────────────────────────────────

    def _print_error(self, message: str) -> None:
        self._state.add_message(MessageType.ERROR, message)
        self._console.print(RichText(f"{ICON_ERROR} {message}", style="error"))

    def _print_warning(self, message: str) -> None:
        self._state.add_message(MessageType.SYSTEM, message)
        self._console.print(RichText(f"{ICON_WARN} {message}", style="warning"))

    # ── Commands ────────────────────────────────────────────────────────

    def _handle_screenshot(self) -> None:
        save_path = f"screenshot_{int(time.time())}.png"
        try:
            import mss
            from PIL import Image
            with mss.mss() as sct:
                monitor = sct.monitors[0]
                shot = sct.grab(monitor)
                img = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
                img.save(save_path)
            self._console.print(f"[dim]Screenshot saved to: {save_path}[/dim]")
        except ImportError as e:
            self._print_error(f"Missing dependency: {e}")
        except Exception as e:
            self._print_error(f"Failed to take screenshot: {e}")

    def _handle_debug(self) -> None:
        self._state.debug_mode = not self._state.debug_mode
        if self._agent is None:
            self._print_warning("No agent initialized yet.")
            return
        status = self._agent.get_status()
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

    def _handle_export(self) -> None:
        save_path = f"conversation_{int(time.time())}.json"
        if self._agent is None:
            self._print_warning("No agent initialized yet.")
            return
        messages = self._agent.conversation.get_messages()
        try:
            with open(save_path, "w", encoding="utf-8") as f:
                json.dump(messages, f, ensure_ascii=False, indent=2)
            self._console.print(f"[dim]Conversation exported to: {save_path}[/dim]")
        except Exception as e:
            self._print_error(f"Failed to export: {e}")

    def _run_command(self, command: str) -> str:
        """Execute a slash command.
        TUI mode: capture output via self._console and return plain text.
        Console mode: print directly to stdout, return ''.
        """
        tui_mode = self._app is not None
        buf = StringIO() if tui_mode else None
        old_file = self._console.file
        if tui_mode:
            self._console.file = buf
        cmd = command.lower().strip()

        if cmd in ("/exit", "/quit"):
            self._console.print("[dim]Goodbye![/dim]")
            self._running = False
        elif cmd == "/clear":
            if self._agent is not None:
                self._agent.reset_conversation()
            self._state.clear_messages()
            self._chat_text = ""
            self._console.print("[dim]Conversation history cleared.[/dim]")
        elif cmd == "/history":
            if self._agent is None:
                self._print_warning("No agent initialized yet.")
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
                        content = msg.get("content", "")
                        if len(content) > 120:
                            content = content[:117] + "..."
                        table.add_row(str(i + 1), role, content)
                    self._console.print(table)
        elif cmd == "/tools":
            if self._agent is None:
                self._print_warning("No agent initialized yet.")
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
        elif cmd == "/help":
            self._console.print(Panel(_COMMANDS_HELP, title="Commands", border_style="green", expand=False))
        elif cmd == "/config":
            parts = command.strip().split(None, 2)
            if len(parts) >= 3 and parts[1].lower() == "set":
                field_name = parts[2].split("=", 1)[0].strip() if "=" in parts[2] else ""
                field_value = parts[2].split("=", 1)[1].strip() if "=" in parts[2] else ""
                if not field_name or not field_value:
                    self._print_warning("Usage: /config set key=value")
                elif self._config.set_field(field_name, field_value):
                    display_val = "****" if field_name == "llm_api_key" else field_value
                    self._console.print(f"[dim]Set {field_name} = {display_val}[/dim]")
                else:
                    self._print_warning(f"Unknown or invalid config field: {field_name}")
            else:
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
        elif cmd == "/status":
            if self._agent is None:
                self._print_warning("No agent initialized yet.")
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
        elif cmd == "/memory clear":
            if self._agent is None:
                self._print_warning("No agent initialized yet.")
            else:
                self._agent.memory.clear()
                self._console.print("[dim]All memories cleared.[/dim]")
        elif cmd == "/memory":
            if self._agent is None:
                self._print_warning("No agent initialized yet.")
            else:
                items = self._agent.memory.get_all()
                if not items:
                    self._console.print("[dim]No memories stored yet.[/dim]")
                else:
                    table = Table(title="User Memory", show_lines=True)
                    table.add_column("Category", style="bold", width=12)
                    table.add_column("Key", width=25)
                    table.add_column("Value", width=40)
                    table.add_column("Access", width=6)
                    for item in sorted(items, key=lambda x: x.category):
                        table.add_row(item.category, item.key, item.value[:60], str(item.access_count))
                    self._console.print(table)
        elif cmd == "/screenshot":
            self._handle_screenshot()
        elif cmd == "/retry":
            if self._last_input:
                self._console.print("[dim]Retrying last input...[/dim]")
                if self._event_task is not None and not self._event_task.done():
                    self._event_task.cancel()
                self._event_task = asyncio.create_task(self._process_events(self._last_input))
            else:
                self._print_warning("No previous input to retry.")
        elif cmd == "/debug":
            self._handle_debug()
        elif cmd == "/export":
            self._handle_export()
        elif cmd == "/compact":
            if self._agent is not None:
                self._agent.conversation.clear()
                self._console.print("[dim]Context compacted (conversation cleared).[/dim]")
        else:
            self._print_warning(f"Unknown command: {command}")

        if tui_mode:
            self._console.file = old_file
            return _strip_ansi(buf.getvalue()) if buf else ""
        return ""

    def _handle_user_command(self, command: str) -> bool:
        """Handle a slash command. Returns True (test-compat entry point).
        Console mode: _run_command already printed to stdout.
        TUI mode: append captured output to the chat buffer.
        """
        output = self._run_command(command)
        if self._app is not None:
            if output:
                self._chat_text += output
            self._rebuild_buffer()
            if not self._running:
                self._app.exit()
        return True

    # ── Key bindings ───────────────────────────────────────────────────

    def _build_key_bindings(self) -> KeyBindings:
        kb = KeyBindings()

        @kb.add("enter")
        def _(event: Any) -> None:
            text = self._input_buffer.strip()
            if not text:
                return
            self._input_buffer = ""
            self._input_cursor = 0
            self._on_input(text)

        @kb.add("c-c")
        def _(event: Any) -> None:
            if self._state.processing:
                self._cancel()
            elif self._input_buffer:
                self._input_buffer = ""
                self._input_cursor = 0
                self._rebuild_buffer()
            else:
                self._running = False
                self._app.exit()

        @kb.add("c-d")
        def _(event: Any) -> None:
            if self._input_cursor < len(self._input_buffer):
                self._input_buffer = (
                    self._input_buffer[:self._input_cursor]
                    + self._input_buffer[self._input_cursor + 1:]
                )
                self._rebuild_buffer()
            elif not self._input_buffer:
                self._running = False
                self._app.exit()

        @kb.add("c-h")
        @kb.add("backspace")
        def _(event: Any) -> None:
            if self._input_cursor > 0:
                self._input_buffer = (
                    self._input_buffer[:self._input_cursor - 1]
                    + self._input_buffer[self._input_cursor:]
                )
                self._input_cursor -= 1
                self._rebuild_buffer()

        @kb.add("left")
        def _(event: Any) -> None:
            if self._input_cursor > 0:
                self._input_cursor -= 1
                self._rebuild_buffer()

        @kb.add("right")
        def _(event: Any) -> None:
            if self._input_cursor < len(self._input_buffer):
                self._input_cursor += 1
                self._rebuild_buffer()

        @kb.add("home")
        @kb.add("c-a")
        def _(event: Any) -> None:
            self._input_cursor = 0
            self._rebuild_buffer()

        @kb.add("end")
        @kb.add("c-e")
        def _(event: Any) -> None:
            self._input_cursor = len(self._input_buffer)
            self._rebuild_buffer()

        @kb.add("c-w")
        def _(event: Any) -> None:
            before = self._input_buffer[:self._input_cursor]
            after = self._input_buffer[self._input_cursor:]
            before = before.rstrip()
            idx = before.rfind(" ")
            before = before[:idx + 1] if idx >= 0 else ""
            self._input_buffer = before + after
            self._input_cursor = len(before)
            self._rebuild_buffer()

        @kb.add("c-u")
        def _(event: Any) -> None:
            self._input_buffer = self._input_buffer[self._input_cursor:]
            self._input_cursor = 0
            self._rebuild_buffer()

        @kb.add("c-k")
        def _(event: Any) -> None:
            self._input_buffer = self._input_buffer[:self._input_cursor]
            self._rebuild_buffer()

        @kb.add("c-y")
        def _(event: Any) -> None:
            import pyperclip
            if self._last_assistant_text:
                pyperclip.copy(self._last_assistant_text)
                self._append_text(f"  Copied to clipboard ({len(self._last_assistant_text)} chars)\n")
                self._rebuild_buffer()

        @kb.add("<any>")
        def _(event: Any) -> None:
            key_press = event.key_sequence[0]
            data = key_press.data
            if not data:
                return
            if ord(data[0]) < 32:
                return
            self._input_buffer = (
                self._input_buffer[:self._input_cursor]
                + data
                + self._input_buffer[self._input_cursor:]
            )
            self._input_cursor += len(data)
            self._rebuild_buffer()

        return kb

    # ── Input handling ─────────────────────────────────────────────────

    def _on_input(self, text: str) -> None:
        if self._confirm_future is not None and not self._confirm_future.done():
            answer = text.strip().lower()
            if answer in ("y", "yes"):
                self._chat_text += "y\n"
                self._rebuild_buffer()
                self._confirm_future.set_result(True)
            elif answer in ("n", "no"):
                self._chat_text += "n\n"
                self._rebuild_buffer()
                self._confirm_future.set_result(False)
            else:
                self._chat_text += "Please answer y or n\nProceed? (y/n): "
                self._rebuild_buffer()
            return

        if text.startswith("/"):
            output = self._run_command(text)
            if output:
                self._chat_text += output
            self._rebuild_buffer()
            if not self._running:
                self._app.exit()
        else:
            self._state.add_message(MessageType.USER, text)
            self._chat_text += f"  {ICON_PROMPT} {text}\n"
            self._rebuild_buffer()
            self._last_input = text
            self._event_task = asyncio.ensure_future(self._process_events(text))

    # ── TUI Init ───────────────────────────────────────────────────────

    def _init_tui(self) -> None:
        self._chat_text = ""
        # read_only Buffer: content updated via set_document(bypass_readonly=True)
        # focusable=True so the Window follows cursor_position and auto-scrolls
        # to the bottom (where the input line lives). Mouse wheel also works.
        self._chat_buffer = Buffer(read_only=True, multiline=True)
        self._chat_control = BufferControl(
            buffer=self._chat_buffer,
            lexer=_ChatLexer(),
            focusable=True,
        )

        self._chat_window = Window(
            content=self._chat_control,
            wrap_lines=True,
            allow_scroll_beyond_bottom=False,
            dont_extend_height=False,
        )

        status_control = FormattedTextControl(self._get_status_text)

        self._layout_container = HSplit([
            self._chat_window,
            Window(height=1, content=status_control, dont_extend_height=True,
                   align=WindowAlign.LEFT, style="class:status"),
        ])

        self._kb = self._build_key_bindings()

        layout = Layout(self._layout_container, focused_element=self._chat_control)

        self._app = Application(
            layout=layout,
            key_bindings=self._kb,
            full_screen=True,
            style=TUI_STYLE,
            mouse_support=True,
        )

    def _get_status_text(self) -> Any:
        spinners = ["\u280b", "\u2819", "\u2839", "\u2838", "\u283c",
                    "\u2834", "\u2826", "\u2827", "\u2807", "\u280f"]
        if self._state.processing:
            spinner = spinners[self._spinner_idx % len(spinners)]
            self._spinner_idx += 1
            op = self._current_op or "thinking..."
            asst_count = sum(
                1 for m in self._state.messages
                if m.type in (MessageType.ASSISTANT, MessageType.THINK)
            )
            return [
                ("class:status.thinking", f" {spinner} {op}"),
                ("", "  "),
                ("class:status.info", f"Tokens: {self._token_count:,}"),
                ("", "  "),
                ("class:status.info", f"Iter: {asst_count}"),
            ]
        else:
            asst_count = sum(
                1 for m in self._state.messages
                if m.type in (MessageType.ASSISTANT, MessageType.THINK)
            )
            return [
                ("class:status.ready", f" {ICON_READY} Ready"),
                ("", "  "),
                ("class:status.info", f"Tokens: {self._token_count:,}"),
                ("", "  "),
                ("class:status.info", f"Iter: {asst_count}"),
                ("", "   "),
                ("class:status.hint", "Ctrl+C cancel  Ctrl+D exit"),
            ]

    # ── Refresh / Cancel ───────────────────────────────────────────────

    async def _refresh_loop(self) -> None:
        while True:
            if self._app:
                if self._agent is not None:
                    status = self._agent.get_status()
                    self._token_count = status.get("total_tokens", 0)
                self._app.invalidate()
            await asyncio.sleep(0.08)

    def _cancel(self) -> None:
        if self._cancelled:
            return
        self._cancelled = True
        if self._agent is not None:
            self._agent.cancel()
        if self._confirm_future is not None and not self._confirm_future.done():
            self._confirm_future.set_result(False)

    # ── Event processing ───────────────────────────────────────────────

    async def _process_events(self, user_input: str) -> None:
        if self._agent is None:
            self._print_error("Agent not initialized.")
            return

        self._cancelled = False
        self._agent.reset_cancelled()
        self._state.processing = True

        if self._app is not None:
            await self._process_events_tui(user_input)
        else:
            await self._process_events_console(user_input)

        self._state.processing = False
        self._current_op = ""

    async def _process_events_tui(self, user_input: str) -> None:
        try:
            async for event in self._agent.run(user_input):
                if self._cancelled:
                    self._append_text(f"{ICON_CANCEL} Cancelled.\n")
                    self._think_active = False
                    self._rebuild_buffer()
                    break

                if event.type == "stream_start":
                    self._stream_text = ""
                    self._stream_start = len(self._chat_text)
                    self._stream_rendered = False
                    self._think_active = False
                    self._think_text = ""
                    self._last_assistant_text = ""
                    self._current_op = "generating..."
                    self._rebuild_buffer()

                elif event.type == "stream_delta":
                    self._stream_text += event.content
                    self._replace_text_range(
                        self._stream_start, f"{ICON_ANSWER} {self._stream_text}"
                    )
                    self._current_op = "generating..."
                    self._rebuild_buffer()

                elif event.type == "stream_think_delta":
                    if not self._think_active:
                        self._think_active = True
                        self._think_start = len(self._chat_text)
                        self._think_text = event.content
                        self._append_text(f"{ICON_THINK} {self._think_text}")
                        self._state.add_message(MessageType.THINK, self._think_text)
                    else:
                        self._think_text += event.content
                        self._replace_text_range(
                            self._think_start, f"{ICON_THINK} {self._think_text}"
                        )
                        if self._state.messages:
                            for m in reversed(self._state.messages):
                                if m.type == MessageType.THINK:
                                    m.content = self._think_text
                                    break
                    self._current_op = "thinking..."
                    self._rebuild_buffer()

                elif event.type == "stream_end":
                    self._think_active = False
                    self._think_text = ""
                    if self._stream_text:
                        self._last_assistant_text = self._stream_text
                        rendered = _render_plain(f"{ICON_ANSWER} {self._stream_text}")
                        self._replace_text_range(self._stream_start, rendered + "\n")
                        self._stream_rendered = True
                        self._state.add_message(MessageType.ASSISTANT, self._stream_text)
                    else:
                        self._replace_text_range(self._stream_start, "")
                    self._stream_text = ""
                    self._current_op = ""
                    self._rebuild_buffer()

                elif event.type == "tool_call":
                    self._think_active = False
                    if event.blocked:
                        self._append_text(f"{ICON_WARN} Blocked: {event.content}\n")
                    else:
                        items = list(event.tool_args.items())
                        if items:
                            k, v = items[0]
                            val = json.dumps(v, ensure_ascii=False)
                            if len(val) > 60:
                                val = val[:57] + "..."
                            self._append_text(f"  {ICON_TOOL} {event.tool_name} {k}={val}\n")
                        else:
                            self._append_text(f"  {ICON_TOOL} {event.tool_name}\n")
                    self._state.add_message(
                        MessageType.TOOL_CALL, f"[{event.tool_name}]",
                        tool_name=event.tool_name, tool_args=event.tool_args,
                    )
                    self._current_op = event.tool_name
                    self._rebuild_buffer()

                elif event.type == "tool_result":
                    result_str = (
                        str(event.tool_result)
                        if event.tool_result is not None
                        else event.content
                    )
                    truncated = result_str[:200]
                    if len(result_str) > 200:
                        truncated += "..."
                    is_error = (
                        isinstance(event.tool_result, dict)
                        and "error" in event.tool_result
                    )
                    icon = ICON_SUCCESS if not is_error else ICON_ERROR
                    self._append_text(f"    {icon} {truncated}\n")
                    self._state.add_message(
                        MessageType.TOOL_RESULT, result_str[:200],
                        tool_name=event.tool_name,
                    )
                    self._rebuild_buffer()

                elif event.type == "final_answer":
                    self._think_active = False
                    if event.content and not self._stream_rendered:
                        self._last_assistant_text = event.content
                        rendered = _render_plain(f"{ICON_ANSWER} {event.content}")
                        self._append_text(rendered + "\n")
                        self._state.add_message(MessageType.ASSISTANT, event.content)
                        self._rebuild_buffer()

                elif event.type == "error":
                    self._think_active = False
                    self._append_text(f"{ICON_ERROR} {event.content}\n")
                    self._state.add_message(MessageType.ERROR, event.content)
                    self._rebuild_buffer()

                elif event.type == "iteration_limit":
                    self._think_active = False
                    self._append_text(f"{ICON_WARN} {event.content}\n")
                    self._rebuild_buffer()

                elif event.type == "cancelled":
                    self._think_active = False

        except asyncio.CancelledError:
            raise
        except Exception as e:
            self._append_text(f"{ICON_ERROR} {e}\n")
        finally:
            self._think_active = False
            self._stream_text = ""
            self._stream_rendered = False
            self._current_op = ""
            self._rebuild_buffer()

    async def _process_events_console(self, user_input: str) -> None:
        streaming_text = ""
        first_content_received = False
        think_active = False
        answer_live: Live | None = None

        def _stop_live() -> None:
            nonlocal answer_live, think_active
            if answer_live:
                answer_live.stop()
                answer_live = None
            if think_active:
                self._console.print()
                think_active = False

        try:
            async for event in self._agent.run(user_input):
                if self._cancelled:
                    _stop_live()
                    self._console.print(
                        RichText(f"{ICON_CANCEL} Cancelled.", style="warning")
                    )
                    break

                if event.type == "stream_start":
                    _stop_live()
                    first_content_received = False
                    streaming_text = ""
                    think_active = False
                    self._console.print(RichText(f"{ICON_ANSWER} ", style="ai_label"))
                    answer_live = Live(
                        RichText(""),
                        console=self._console,
                        refresh_per_second=15,
                        transient=False,
                    )
                    answer_live.start()

                elif event.type == "stream_delta":
                    streaming_text += event.content
                    if think_active:
                        self._console.print()
                        think_active = False
                    if not first_content_received:
                        first_content_received = True
                    if answer_live is not None:
                        answer_live.update(RichText(streaming_text))

                elif event.type == "stream_think_delta":
                    output = (
                        answer_live.console if answer_live is not None else self._console
                    )
                    if not think_active:
                        think_active = True
                        output.print()
                        output.print(RichText(f"{ICON_THINK} ", style="dim"), end="")
                    output.print(RichText(event.content, style="dim"), end="")

                elif event.type == "stream_end":
                    if answer_live is not None:
                        if streaming_text:
                            answer_live.update(Markdown(streaming_text))
                        answer_live.stop()
                        answer_live = None
                    think_active = False

                elif event.type == "tool_call":
                    _stop_live()
                    if event.blocked:
                        self._print_warning(f"Blocked: {event.content}")
                    else:
                        self._state.add_message(
                            MessageType.TOOL_CALL, f"[{event.tool_name}]",
                            tool_name=event.tool_name, tool_args=event.tool_args,
                        )
                        items = list(event.tool_args.items())
                        if items:
                            first_k, first_v = items[0]
                            val_str = json.dumps(first_v, ensure_ascii=False)
                            if len(val_str) > 60:
                                val_str = val_str[:57] + "..."
                            self._console.print(
                                RichText(
                                    f"  {ICON_TOOL} {event.tool_name} {first_k}={val_str}",
                                    style="tool_icon",
                                )
                            )
                        else:
                            self._console.print(
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
                    self._state.add_message(
                        MessageType.TOOL_RESULT, result_str[:200],
                        tool_name=event.tool_name,
                    )
                    self._console.print(RichText(f"    {icon} {truncated}", style=style))

                elif event.type == "final_answer":
                    if not first_content_received and event.content:
                        self._console.print(Markdown(event.content))

                elif event.type == "error":
                    _stop_live()
                    self._print_error(event.content)

                elif event.type == "iteration_limit":
                    _stop_live()
                    self._print_warning(event.content)

                elif event.type == "cancelled":
                    pass

        except asyncio.CancelledError:
            raise
        except KeyboardInterrupt:
            self._cancel()

    async def ask_input(self, prompt: str, password_mode: bool = False) -> str | None:
        self._console.print(RichText(f"! {prompt}", style="warning"))
        import getpass

        def _get_input() -> str | None:
            try:
                if password_mode:
                    return getpass.getpass("Password: ")
                return input("Input: ")
            except (EOFError, KeyboardInterrupt):
                return None

        return await asyncio.to_thread(_get_input)

    def _show_welcome(self) -> None:
        """Show welcome message. Console mode prints to stdout; TUI mode updates buffer."""
        if self._app is not None:
            self._show_welcome_tui()
        else:
            self._show_welcome_console()

    def _show_welcome_console(self) -> None:
        self._console.print(_WELCOME_ART, style="bold cyan")
        from pc_assistant import __version__
        self._console.print(f"  v{__version__}  {ICON_BULLET}  Type /help for commands")

    def _show_welcome_tui(self) -> None:
        self._chat_text = _WELCOME_ART + "\n"
        from pc_assistant import __version__
        self._chat_text += f"  v{__version__}  {ICON_BULLET}  Type /help for commands\n\n"
        self._rebuild_buffer()

    async def run(self) -> None:
        self._running = True
        self._init_tui()
        self._show_welcome_tui()

        loop = asyncio.get_event_loop()
        try:
            loop.add_signal_handler(signal.SIGINT, self._cancel)
        except (NotImplementedError, OSError):
            pass

        self._refresh_task = asyncio.get_event_loop().create_task(self._refresh_loop())

        try:
            await self._app.run_async()
        finally:
            self._refresh_task.cancel()
            try:
                await self._refresh_task
            except asyncio.CancelledError:
                pass
            try:
                loop.remove_signal_handler(signal.SIGINT)
            except (NotImplementedError, OSError):
                pass
