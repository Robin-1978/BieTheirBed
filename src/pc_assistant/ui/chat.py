from __future__ import annotations

import asyncio
import json
import signal
import time
from io import StringIO
from typing import Any, Callable

from pc_assistant.config import AppConfig
from pc_assistant.agent import Agent, AgentEvent
from pc_assistant.ui.state import UIState, Message, MessageType
from pc_assistant.ui.theme import TOKYO_NIGHT

from rich.console import Console
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from prompt_toolkit.application import Application
from prompt_toolkit.formatted_text import ANSI as PTANSI, FormattedText, StyleAndTextTuples, merge_formatted_text, to_formatted_text
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import Layout, HSplit, Window, WindowAlign
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.styles import Style as PTStyle
from prompt_toolkit.widgets import TextArea

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
/debug          Toggle debug mode
/export         Export conversation to file
/compact        Compact context (remove old messages)\
"""

TUI_STYLE = PTStyle.from_dict({
    "input": "fg:#9ece6a",
    "status": "bg:#1a1b26 fg:#565f89",
    "status.ready": "bold fg:#9ece6a",
    "status.thinking": "bold fg:#7aa2f7",
    "status.executing": "bold fg:#e0af68",
    "status.info": "italic fg:#565f89",
    "user_text": "bold fg:#9ece6a",
})


class _ChatWindow(Window):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.pinned_to_bottom = True

    def _scroll(self, ui_content: Any, width: int, height: int) -> None:
        super()._scroll(ui_content, width, height)
        if self.pinned_to_bottom:
            total_lines = ui_content.line_count
            max_scroll = max(0, total_lines - height)
            self.vertical_scroll = max_scroll

    def _scroll_up(self) -> None:
        self.pinned_to_bottom = False
        super()._scroll_up()

    def _scroll_down(self) -> None:
        self.pinned_to_bottom = False
        super()._scroll_down()


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
        self._state = UIState()
        self._console = Console(theme=TOKYO_NIGHT)
        self._last_input: str = ""
        self._cancelled = False
        self._event_task: asyncio.Task | None = None

        # TUI components (lazy init)
        self._app: Application | None = None
        self._chat_window: _ChatWindow | None = None
        self._input_field: TextArea | None = None
        self._kb: KeyBindings | None = None
        self._refresh_task: asyncio.Task | None = None
        self._spinner_idx = 0
        self._console_buffer: StringIO | None = None
        self._saved_console_file: Any = None

        # TUI rendering state
        self._chat_fragments: list[StyleAndTextTuples] = []
        self._current_op = ""
        self._token_count = 0

    def set_agent(self, agent: Agent) -> None:
        self._agent = agent

    def _show_welcome(self) -> None:
        self._console.print(_WELCOME_ART, style="bold green", highlight=False)
        from pc_assistant import __version__
        self._console.print(f"  [bold]v{__version__}[/bold]  \u2022  Type [bold]/help[/bold] for commands\n")

    def _print_tool_call(self, name: str, arguments: dict[str, Any]) -> None:
        self._state.add_message(MessageType.TOOL_CALL, f"[{name}]", tool_name=name, tool_args=arguments)
        items = list(arguments.items())
        if items:
            first_k, first_v = items[0]
            val_str = json.dumps(first_v, ensure_ascii=False)
            if len(val_str) > 60:
                val_str = val_str[:57] + "..."
            self._console.print(Text(f"  \u2699 {name} {first_k}={val_str}", style="tool_icon"))
        else:
            self._console.print(Text(f"  \u2699 {name}", style="tool_icon"))

    def _print_tool_result(self, name: str, result: str, is_error: bool = False) -> None:
        self._state.add_message(MessageType.TOOL_RESULT, result[:200], tool_name=name)
        truncated = result[:200]
        if len(result) > 200:
            truncated += "..."
        icon = "\u2717" if is_error else "\u2713"
        style = "error" if is_error else "tool_result"
        self._console.print(Text(f"    {icon} {truncated}", style=style))

    def _print_error(self, message: str) -> None:
        self._state.add_message(MessageType.ERROR, message)
        self._console.print(Text(f"\u2717 {message}", style="error"))

    def _print_warning(self, message: str) -> None:
        self._state.add_message(MessageType.SYSTEM, message)
        self._console.print(Text(f"! {message}", style="warning"))

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

    def _handle_user_command(self, command: str) -> bool:
        cmd = command.lower().strip()

        if cmd in ("/exit", "/quit"):
            self._console.print("[dim]Goodbye![/dim]")
            self._running = False
            return True

        if cmd == "/clear":
            if self._agent is not None:
                self._agent.reset_conversation()
            self._state.clear_messages()
            self._chat_fragments.clear()
            self._console.print("[dim]Conversation history cleared.[/dim]")
            return True

        if cmd == "/history":
            if self._agent is None:
                self._print_warning("No agent initialized yet.")
                return True
            messages = self._agent.conversation.get_messages()
            if not messages:
                self._console.print("[dim]No conversation history.[/dim]")
                return True
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
            return True

        if cmd == "/tools":
            if self._agent is None:
                self._print_warning("No agent initialized yet.")
                return True
            tools = self._agent.registry.list_tools()
            if not tools:
                self._console.print("[dim]No tools registered.[/dim]")
                return True
            table = Table(title="Available Tools")
            table.add_column("Tool", style="cyan bold")
            for t in tools:
                table.add_row(t)
            self._console.print(table)
            return True

        if cmd == "/help":
            self._console.print(Panel(_COMMANDS_HELP, title="Commands", border_style="green", expand=False))
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
                    self._console.print(f"[dim]Set {field_name} = {display_val}[/dim]")
                else:
                    self._print_warning(f"Unknown or invalid config field: {field_name}")
                return True
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
            return True

        if cmd == "/status":
            if self._agent is None:
                self._print_warning("No agent initialized yet.")
                return True
            status = self._agent.get_status()
            table = Table(title="Agent Status", show_lines=True)
            table.add_column("Property", style="bold")
            table.add_column("Value")
            for k, v in status.items():
                if isinstance(v, list):
                    v = ", ".join(str(x) for x in v)
                table.add_row(k, str(v))
            self._console.print(table)
            return True

        if cmd == "/memory clear":
            if self._agent is None:
                self._print_warning("No agent initialized yet.")
                return True
            self._agent.memory.clear()
            self._console.print("[dim]All memories cleared.[/dim]")
            return True

        if cmd == "/memory":
            if self._agent is None:
                self._print_warning("No agent initialized yet.")
                return True
            items = self._agent.memory.get_all()
            if not items:
                self._console.print("[dim]No memories stored yet.[/dim]")
                return True
            table = Table(title="User Memory", show_lines=True)
            table.add_column("Category", style="bold", width=12)
            table.add_column("Key", width=25)
            table.add_column("Value", width=40)
            table.add_column("Access", width=6)
            for item in sorted(items, key=lambda x: x.category):
                table.add_row(item.category, item.key, item.value[:60], str(item.access_count))
            self._console.print(table)
            return True

        if cmd == "/screenshot":
            self._handle_screenshot()
            return True

        if cmd == "/retry":
            if self._last_input:
                self._console.print("[dim]Retrying last input...[/dim]")
                if self._event_task is not None and not self._event_task.done():
                    self._event_task.cancel()
                self._event_task = asyncio.create_task(self._process_events(self._last_input))
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
                self._console.print("[dim]Context compacted (conversation cleared).[/dim]")
            return True

        self._print_warning(f"Unknown command: {command}")
        return True

    def _build_key_bindings(self) -> KeyBindings:
        kb = KeyBindings()

        @kb.add("enter")
        def _(event: Any) -> None:
            buffer = event.current_buffer
            text = buffer.text.strip()
            if not text:
                return
            buffer.text = ""
            self._on_input(text)

        @kb.add("c-c")
        def _(event: Any) -> None:
            if self._state.processing:
                self._cancel()
            elif self._input_field and self._input_field.buffer.text:
                self._input_field.buffer.text = ""
            else:
                self._running = False
                self._app.exit()

        @kb.add("c-d")
        def _(event: Any) -> None:
            self._running = False
            self._app.exit()

        @kb.add("pageup")
        def _(event: Any) -> None:
            if self._chat_window:
                self._chat_window.pinned_to_bottom = False
                vs = self._chat_window.vertical_scroll or 0
                ri = self._chat_window.render_info
                page = ri.window_height if ri else 20
                self._chat_window.vertical_scroll = max(0, vs - page)

        @kb.add("pagedown")
        def _(event: Any) -> None:
            if self._chat_window:
                self._chat_window.pinned_to_bottom = False
                vs = self._chat_window.vertical_scroll or 0
                ri = self._chat_window.render_info
                page = ri.window_height if ri else 20
                self._chat_window.vertical_scroll = min(10 ** 9, vs + page)

        @kb.add("end")
        def _(event: Any) -> None:
            if self._chat_window:
                self._chat_window.pinned_to_bottom = True

        return kb

    def _on_input(self, text: str) -> None:
        if text.startswith("/"):
            self._handle_user_command(text)
            if not self._running:
                self._app.exit()
        else:
            self._state.add_message(MessageType.USER, text)
            self._chat_fragments.append([("bold #9ece6a", f"  \u276f {text}\n")])
            self._last_input = text
            self._event_task = asyncio.ensure_future(self._process_events(text))

    def _render_md(self, text: str) -> StyleAndTextTuples:
        """Render markdown text to prompt_toolkit styled fragments."""
        buf = StringIO()
        console = Console(
            theme=TOKYO_NIGHT,
            file=buf,
            force_terminal=True,
            color_system="truecolor",
            width=80,
        )
        console.print(Markdown(text))
        return self._clean_styles(list(to_formatted_text(PTANSI(buf.getvalue()))))

    def _init_tui(self) -> None:
        self._chat_fragments.clear()
        self._console_buffer = StringIO()
        self._saved_console_file = self._console.file
        self._console.file = self._console_buffer

        chat_control = FormattedTextControl(self._get_chat_fragments)

        self._chat_window = _ChatWindow(
            content=chat_control,
            wrap_lines=True,
            dont_extend_height=False,
        )

        self._input_field = TextArea(
            height=1,
            multiline=False,
            style="class:input",
        )

        status_control = FormattedTextControl(self._get_status_text)

        self._layout = HSplit([
            self._chat_window,
            self._input_field,
            Window(height=1, content=status_control, dont_extend_height=True,
                   align=WindowAlign.LEFT, style="class:status"),
        ])

        self._kb = self._build_key_bindings()

        layout = Layout(self._layout, focused_element=self._input_field)

        self._app = Application(
            layout=layout,
            key_bindings=self._kb,
            full_screen=True,
            style=TUI_STYLE,
            mouse_support=True,
        )

    def _clean_styles(self, fragments: StyleAndTextTuples) -> StyleAndTextTuples:
        """Remove unsupported style tokens (e.g. 'dim') from parsed ANSI."""
        result: StyleAndTextTuples = []
        for style, text in fragments:
            if style and "dim" in style:
                style = style.replace(" dim", "").replace("dim ", "").replace("dim", "")
                if not style.strip():
                    style = ""
            result.append((style, text))
        return result

    def _sync_console_to_chat(self) -> None:
        if self._console_buffer is None:
            return
        text = self._console_buffer.getvalue()
        if text:
            fragments = list(to_formatted_text(PTANSI(text)))
            self._chat_fragments.append(self._clean_styles(fragments))
            self._console_buffer = StringIO()
            self._console.file = self._console_buffer

    def _get_chat_fragments(self) -> StyleAndTextTuples:
        self._sync_console_to_chat()
        parts: list[StyleAndTextTuples] = list(self._chat_fragments)
        return merge_formatted_text(parts) if parts else FormattedText([("", "")])

    def _get_status_text(self) -> StyleAndTextTuples:
        spinners = ["\u280b", "\u2819", "\u2839", "\u2838", "\u283c",
                    "\u2834", "\u2826", "\u2827", "\u2807", "\u280f"]
        if self._state.processing:
            spinner = spinners[self._spinner_idx % len(spinners)]
            self._spinner_idx += 1
            op = self._current_op or "thinking..."
            asst_count = sum(1 for m in self._state.messages if m.type in (
                MessageType.ASSISTANT, MessageType.THINK))
            return FormattedText([
                ("class:status.thinking", f"{spinner} {op}"),
                ("", "  "),
                ("class:status.info", f"Tokens: {self._token_count:,}"),
                ("", "  "),
                ("class:status.info", f"Iter: {asst_count}"),
            ])
        else:
            asst_count = sum(1 for m in self._state.messages if m.type in (
                MessageType.ASSISTANT, MessageType.THINK))
            return FormattedText([
                ("class:status.ready", "\u25cf Ready"),
                ("", "  "),
                ("class:status.info", f"Tokens: {self._token_count:,}"),
                ("", "  "),
                ("class:status.info", f"Iter: {asst_count}"),
                ("", "   "),
                ("class:status.info", "Ctrl+C cancel  Ctrl+D exit"),
            ])

    async def _refresh_loop(self) -> None:
        while True:
            if self._app:
                self._app.invalidate()
            await asyncio.sleep(0.08)

    def _cancel(self) -> None:
        if self._cancelled:
            return
        self._cancelled = True
        if self._agent is not None:
            self._agent.cancel()

    async def _process_events(self, user_input: str) -> None:
        if self._agent is None:
            self._print_error("Agent not initialized.")
            return

        self._cancelled = False
        self._agent.reset_cancelled()
        self._state.processing = True

        loop = asyncio.get_event_loop()
        try:
            loop.add_signal_handler(signal.SIGINT, self._cancel)
        except (NotImplementedError, OSError):
            pass

        # Detect if we are in TUI mode
        if self._app is not None:
            await self._process_events_tui(user_input)
        else:
            await self._process_events_console(user_input)

        self._state.processing = False
        self._current_op = ""
        try:
            loop.remove_signal_handler(signal.SIGINT)
        except (NotImplementedError, OSError):
            pass

    async def _process_events_tui(self, user_input: str) -> None:
        streaming_text = ""
        think_active = False

        try:
            async for event in self._agent.run(user_input):
                if self._cancelled:
                    self._chat_fragments.append([("bold #f7768e", "! Operation cancelled.\n")])
                    break

                if event.type == "stream_start":
                    streaming_text = ""
                    self._chat_fragments.append([("bold #7aa2f7", "\u25c6 ")])
                    think_active = False
                    self._current_op = "generating..."
                    self._app.invalidate()

                elif event.type == "stream_delta":
                    streaming_text += event.content
                    if self._chat_fragments:
                        self._chat_fragments[-1] = [
                            ("bold #7aa2f7", "\u25c6 "),
                            ("", streaming_text),
                        ]
                    if think_active:
                        self._chat_fragments.append([("", "\n")])
                        think_active = False
                    self._app.invalidate()

                elif event.type == "stream_think_delta":
                    if not think_active:
                        think_active = True
                        self._chat_fragments.append([("italic #565f89", "\U0001f4ad ")])
                    else:
                        last = self._chat_fragments[-1] if self._chat_fragments else None
                        if last and len(last) == 1 and last[0][0] == "italic #565f89":
                            last[0] = ("italic #565f89", last[0][1] + event.content)
                        else:
                            self._chat_fragments.append([("italic #565f89", event.content)])
                    self._current_op = "thinking..."

                elif event.type == "stream_end":
                    if streaming_text:
                        rendered = self._render_md(f"\u25c6 {streaming_text}")
                        if self._chat_fragments:
                            self._chat_fragments[-1] = rendered
                        else:
                            self._chat_fragments.append(rendered)
                    self._current_op = ""
                    self._app.invalidate()

                elif event.type == "tool_call":
                    if event.blocked:
                        self._chat_fragments.append([("bold #e0af68", f"! Blocked: {event.content}\n")])
                    else:
                        items = list(event.tool_args.items())
                        if items:
                            k, v = items[0]
                            val = json.dumps(v, ensure_ascii=False)
                            if len(val) > 60:
                                val = val[:57] + "..."
                            self._chat_fragments.append([
                                ("bold #73daca", f"  \u2699 "),
                                ("bold #7aa2f7", event.tool_name),
                                ("#73daca", f" {k}={val}\n"),
                            ])
                        else:
                            self._chat_fragments.append([
                                ("bold #73daca", f"  \u2699 {event.tool_name}\n"),
                            ])
                    self._state.add_message(MessageType.TOOL_CALL, f"[{event.tool_name}]",
                                            tool_name=event.tool_name, tool_args=event.tool_args)
                    self._current_op = event.tool_name
                    self._app.invalidate()

                elif event.type == "tool_result":
                    result_str = str(event.tool_result) if event.tool_result is not None else event.content
                    truncated = result_str[:200]
                    if len(result_str) > 200:
                        truncated += "..."
                    is_error = isinstance(event.tool_result, dict) and "error" in event.tool_result
                    icon = "\u2713" if not is_error else "\u2717"
                    style = "#9ece6a" if not is_error else "#f7768e"
                    self._chat_fragments.append([(f"bold {style}", f"    {icon} {truncated}\n")])
                    self._state.add_message(MessageType.TOOL_RESULT, result_str[:200],
                                            tool_name=event.tool_name)
                    self._app.invalidate()

                elif event.type == "final_answer":
                    if event.content:
                        rendered = self._render_md(f"\u25c6 {event.content}")
                        self._chat_fragments.append(rendered)
                        self._app.invalidate()

                elif event.type == "error":
                    self._chat_fragments.append([("bold #f7768e", f"\u2717 {event.content}\n")])
                    self._state.add_message(MessageType.ERROR, event.content)
                    self._app.invalidate()

                elif event.type == "iteration_limit":
                    self._chat_fragments.append([("bold #e0af68", f"! {event.content}\n")])

                elif event.type == "cancelled":
                    pass

        except asyncio.CancelledError:
            raise
        except Exception as e:
            self._chat_fragments.append([("bold #f7768e", f"\u2717 {e}\n")])
        finally:
            self._current_op = ""
            self._app.invalidate()

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
                    self._console.print(Text("! Operation cancelled.", style="warning"))
                    break

                if event.type == "stream_start":
                    _stop_live()
                    first_content_received = False
                    streaming_text = ""
                    think_active = False
                    self._console.print(Text("\u25c6 ", style="ai_label"))
                    answer_live = Live(
                        Text(""),
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
                        answer_live.update(Text(streaming_text))

                elif event.type == "stream_think_delta":
                    output = answer_live.console if answer_live is not None else self._console
                    if not think_active:
                        think_active = True
                        output.print()
                        output.print(Text("\U0001f4ad ", style="dim"), end="")
                    output.print(Text(event.content, style="dim"), end="")

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
                        self._print_tool_call(event.tool_name, event.tool_args)

                elif event.type == "tool_result":
                    result_str = str(event.tool_result) if event.tool_result is not None else event.content
                    is_error = isinstance(event.tool_result, dict) and "error" in event.tool_result
                    self._print_tool_result(event.tool_name, result_str, is_error)

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
        self._console.print(Text(f"! {prompt}", style="warning"))
        if password_mode:
            import getpass
            return getpass.getpass("Password: ")
        try:
            return input("Input: ")
        except (EOFError, KeyboardInterrupt):
            return None

    def _show_welcome_tui(self) -> None:
        self._chat_fragments.append([("bold #9ece6a", _WELCOME_ART)])
        from pc_assistant import __version__
        self._chat_fragments.append([
            ("", f"  "),
            ("bold", f"v{__version__}"),
            ("#565f89", f"  \u2022  Type "),
            ("bold", "/help"),
            ("#565f89", " for commands\n\n"),
        ])

    async def run(self) -> None:
        self._running = True
        self._init_tui()
        self._show_welcome_tui()

        self._refresh_task = asyncio.get_event_loop().create_task(self._refresh_loop())

        try:
            await self._app.run_async()
        finally:
            self._refresh_task.cancel()
            try:
                await self._refresh_task
            except asyncio.CancelledError:
                pass
            if self._saved_console_file is not None:
                self._console.file = self._saved_console_file
                self._saved_console_file = None
