from __future__ import annotations

import asyncio
import json
import signal
import time
from typing import Any, Callable

from pc_assistant.config import AppConfig
from pc_assistant.agent import Agent, AgentEvent
from pc_assistant.ui.state import UIState, Message, MessageType
from pc_assistant.ui.theme import TOKYO_NIGHT

from rich.console import Console
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.text import Text
from rich.table import Table
from rich.prompt import Prompt

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

    def _show_welcome(self) -> None:
        self._console.print(_WELCOME_ART, style="bold green", highlight=False)
        from pc_assistant import __version__
        self._console.print(f"  [bold]v{__version__}[/bold]  •  Type [bold]/help[/bold] for commands\n")

    def _print_tool_call(self, name: str, arguments: dict[str, Any]) -> None:
        self._state.add_message(MessageType.TOOL_CALL, f"[{name}]", tool_name=name, tool_args=arguments)
        items = list(arguments.items())
        if items:
            first_k, first_v = items[0]
            val_str = json.dumps(first_v, ensure_ascii=False)
            if len(val_str) > 60:
                val_str = val_str[:57] + "..."
            self._console.print(Text(f"  ⚙ {name} {first_k}={val_str}", style="tool_icon"))
        else:
            self._console.print(Text(f"  ⚙ {name}", style="tool_icon"))

    def _print_tool_result(self, name: str, result: str, is_error: bool = False) -> None:
        self._state.add_message(MessageType.TOOL_RESULT, result[:200], tool_name=name)
        truncated = result[:200]
        if len(result) > 200:
            truncated += "..."
        icon = "✗" if is_error else "✓"
        style = "error" if is_error else "tool_result"
        self._console.print(Text(f"    {icon} {truncated}", style=style))

    def _print_error(self, message: str) -> None:
        self._state.add_message(MessageType.ERROR, message)
        self._console.print(Text(f"✗ {message}", style="error"))

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
                self._console.print("[dim]Context compacted (conversation cleared).[/dim]")
            return True

        self._print_warning(f"Unknown command: {command}")
        return True

    async def _process_events(self, user_input: str) -> None:
        if self._agent is None:
            self._print_error("Agent not initialized.")
            return

        self._cancelled = False
        self._agent._cancelled = False

        loop = asyncio.get_event_loop()
        try:
            loop.add_signal_handler(signal.SIGINT, self._cancel)
        except (NotImplementedError, OSError):
            pass

        streaming_text = ""
        first_content_received = False
        think_start_time: float | None = None
        think_text = ""
        live: Live | None = None

        try:
            async for event in self._agent.run(user_input):
                if self._cancelled:
                    self._console.print(Text("! Operation cancelled.", style="warning"))
                    break

                if event.type == "stream_start":
                    first_content_received = False

                elif event.type == "think_start":
                    think_start_time = time.time()
                    think_text = ""
                    self._console.print(Text("◇ ", style="think_icon"), end="")
                    self._console.print(Text("Thinking...", style="think_dim"), end="")

                elif event.type == "stream_think_delta":
                    think_text += event.content

                elif event.type == "think_end":
                    elapsed = time.time() - think_start_time if think_start_time else 0
                    self._console.print(Text(f" {elapsed:.1f}s", style="think_dim"))
                    think_start_time = None
                    if think_text.strip():
                        self._console.print(Panel(
                            Text(think_text.strip(), style="think_dim"),
                            border_style="think_icon",
                            padding=(0, 1),
                            expand=False,
                        ))
                    think_text = ""

                elif event.type == "stream_delta":
                    if not first_content_received:
                        first_content_received = True
                        self._console.print()
                        self._console.print(Text("◆ ", style="ai_label"), end="")
                        live = Live(console=self._console, refresh_per_second=12, vertical_overflow="visible")
                        live.start()
                    streaming_text += event.content
                    if live is not None:
                        live.update(Markdown(streaming_text))

                elif event.type == "stream_end":
                    if live is not None:
                        live.update(Markdown(streaming_text))
                        live.stop()
                        live = None

                elif event.type == "thought":
                    if event.content and event.content.strip():
                        self._console.print(Panel(
                            Text(event.content.strip(), style="think_dim"),
                            border_style="think_icon",
                            padding=(0, 1),
                            expand=False,
                        ))

                elif event.type == "tool_call":
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
                        self._console.print()
                        self._console.print(Text("◆ ", style="ai_label"))
                        self._console.print(Markdown(event.content))

                elif event.type == "error":
                    self._print_error(event.content)

                elif event.type == "iteration_limit":
                    self._print_warning(event.content)

                elif event.type == "cancelled":
                    pass

        except KeyboardInterrupt:
            self._cancel()
        finally:
            if live is not None:
                live.stop()
            try:
                loop.remove_signal_handler(signal.SIGINT)
            except (NotImplementedError, OSError):
                pass

    def _cancel(self) -> None:
        if self._cancelled:
            return
        self._cancelled = True
        if self._agent is not None:
            self._agent.cancel()

    async def ask_input(self, prompt: str, password_mode: bool = False) -> str | None:
        self._console.print(Text(f"! {prompt}", style="warning"))
        if password_mode:
            import getpass
            return getpass.getpass("Password: ")
        try:
            return input("Input: ")
        except (EOFError, KeyboardInterrupt):
            return None

    async def run(self) -> None:
        self._running = True
        self._show_welcome()

        while self._running:
            try:
                user_input = Prompt.ask("[prompt]❯[/prompt]", console=self._console)
            except (EOFError, KeyboardInterrupt):
                self._console.print("\n[dim]Goodbye![/dim]")
                break

            if user_input is None:
                break
            user_input = user_input.strip()
            if not user_input:
                continue

            if user_input.startswith("/"):
                self._handle_user_command(user_input)
                if not self._running:
                    break
            else:
                self._last_input = user_input
                await self._process_events(user_input)
