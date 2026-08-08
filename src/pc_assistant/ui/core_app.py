"""Textual client for the strict Core API."""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any

from textual import work
from textual.app import App, ComposeResult
from textual.containers import Vertical, VerticalScroll
from textual.widgets import Header, Markdown, Static

from pc_assistant.agent_runtime.contracts import RunEvent
from pc_assistant.artifacts.delivery import save_download
from pc_assistant.config import AppConfig
from pc_assistant.service.core_api import ConfirmationRequestedMessage
from pc_assistant.service.core_client import CoreClient
from pc_assistant.ui.clipboard import copy_or_save
from pc_assistant.ui.state import MessageType, UIState
from pc_assistant.ui.theme import AVAILABLE_THEMES, get_palette, set_theme
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


_CONFIRM_TIMEOUT = 120.0
_WELCOME = """\
# PC Assistant

Type a message to chat, or use `/help` for commands.
"""
_HELP = """\
| Command | Description |
|---------|-------------|
| `/new` | Start a new conversation |
| `/memory` | Show remembered user preferences |
| `/memory clear` | Clear memories |
| `/history` | Show conversation history |
| `/tools` | List available tools |
| `/status` | Show Core status |
| `/config set key=value` | Persist a supported setting |
| `/export` | Export conversation |
| `/theme [name]` | Show or switch theme |
| `/confirm`, `/deny` | Resolve a pending tool confirmation |
| `/exit`, `/quit` | Exit |
"""


class CoreChatApp(App):
    CSS_PATH = "chat.tcss"
    TITLE = "PC Assistant"
    BINDINGS = [
        ("escape", "cancel_turn", "Cancel current turn"),
        ("ctrl+d", "quit", "Quit"),
        ("ctrl+shift+c", "copy_last", "Copy last answer"),
    ]

    def __init__(
        self,
        config: AppConfig,
        client: CoreClient,
        session_handle: str,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self._config = config
        self._client = client
        self._session_handle = session_handle
        self._state = UIState()
        self._processing = False
        self._cancelled = False
        self._last_input = ""
        self._last_answer = ""
        self._current_tool_panel: ToolCallPanel | None = None
        self._confirm_pending: asyncio.Future[bool] | None = None
        set_theme(config.ui_theme)

    def get_css_variables(self) -> dict[str, str]:
        return {**super().get_css_variables(), **get_palette()}

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        yield VerticalScroll(id="chat-log")
        yield Vertical(
            ChatInput(id="user-input"),
            Static(f" {ICON_READY} Ready  |  /help for commands", id="status-bar"),
            id="bottom-bar",
        )

    def on_mount(self) -> None:
        self.query_one("#chat-log", VerticalScroll).mount(CommandOutput(_WELCOME))
        self.query_one("#user-input", ChatInput).focus()
        self._client.set_confirmation_handler(self._confirm_tool)

    def on_unmount(self) -> None:
        self._client.set_confirmation_handler(None)
        pending = self._confirm_pending
        if pending is not None and not pending.done():
            pending.set_result(False)

    def on_chat_input_submitted(self, event: ChatInput.Submitted) -> None:
        text = event.value.strip()
        if not text:
            return
        if text.startswith("/"):
            self.run_worker(self._handle_command(text))
        else:
            self._last_input = text
            self._run_turn(text)

    def on_mouse_down(self, event: Any) -> None:
        if getattr(event, "button", 0) != 3:
            return
        selected = self._selected_text()
        if not selected:
            return
        try:
            event.prevent_default()
            event.stop()
        except Exception:
            pass
        self._copy_worker(selected)

    def _selected_text(self) -> str:
        try:
            selected = self.screen.get_selected_text() or ""
            return selected if selected.strip() else ""
        except Exception:
            return ""

    @work(exclusive=True)
    async def _run_turn(self, text: str) -> None:
        self._processing = True
        self._cancelled = False
        self._set_status("thinking…")
        log = self.query_one("#chat-log", VerticalScroll)
        await log.mount(UserMessage(text))
        self._state.add_message(MessageType.USER, text)
        response = AssistantMessage()
        await log.mount(response)
        stream = Markdown.get_stream(response.markdown)
        answer_parts: list[str] = []
        try:
            async for event in self._client.run(self._session_handle, text):
                if self._cancelled:
                    break
                await self._handle_event(event, response, stream, answer_parts)
        except Exception as exc:
            await stream.write(f"\n\n{ICON_ERROR} **Error:** {exc}\n")
            self._state.add_message(MessageType.ERROR, str(exc))
        finally:
            await stream.stop()
            self._processing = False
            self._set_status("Ready")
            log.scroll_end(animate=False)

    async def _handle_event(
        self,
        event: RunEvent,
        response: AssistantMessage,
        stream: Any,
        answer_parts: list[str],
    ) -> None:
        payload = event.payload
        if event.event_type == "content_delta":
            answer_parts.append(payload.content)
            await stream.write(payload.content)
            self._set_status("generating…")
        elif event.event_type == "final_output":
            answer_parts[:] = [payload.content]
            self._last_answer = payload.content.strip()
        elif event.event_type == "reasoning_delta":
            response.add_thinking(payload.content)
            self._set_status("thinking…")
        elif event.event_type == "tool_call":
            self._current_tool_panel = response.add_tool_call(
                payload.tool_name,
                payload.tool_args,
                blocked=payload.blocked,
                block_reason=payload.content if payload.blocked else "",
            )
            self._state.add_message(
                MessageType.TOOL_CALL,
                f"[{payload.tool_name}]",
                tool_name=payload.tool_name,
                tool_args=payload.tool_args,
            )
            self._set_status(payload.tool_name)
        elif event.event_type == "tool_result":
            result = payload.tool_result
            rendered = json.dumps(result, ensure_ascii=False) if isinstance(result, dict) else str(result)
            if self._current_tool_panel is not None:
                self._current_tool_panel.set_result(
                    rendered,
                    is_error=payload.blocked,
                )
                self._current_tool_panel = None
            self._state.add_message(
                MessageType.TOOL_RESULT,
                rendered[:200],
                tool_name=payload.tool_name,
            )
        elif event.event_type == "artifact" and payload.artifact is not None:
            try:
                downloaded = await self._client.download_artifact(
                    self._session_handle,
                    payload.artifact.artifact_id,
                )
                target = await asyncio.to_thread(
                    save_download,
                    downloaded,
                    Path(self._config.runtime_root) / "downloads",
                )
            except Exception as exc:
                warning = f"Artifact download failed: {exc}"
                await stream.write(f"\n\n{ICON_WARN} *{warning}*\n")
                self._state.add_message(MessageType.SYSTEM, warning)
            else:
                await stream.write(
                    f"\n\n*Artifact: `{payload.artifact.name}` "
                    f"saved to `{target}`*\n"
                )
        elif event.event_type == "completed":
            self._last_answer = "".join(answer_parts).strip()
            self._state.add_message(MessageType.ASSISTANT, self._last_answer)
        elif event.event_type == "cancelled":
            await stream.write("\n\n*Cancelled.*\n")
        elif event.event_type == "failed":
            await stream.write(f"\n\n{ICON_ERROR} **Error:** {payload.content}\n")
            self._state.add_message(MessageType.ERROR, payload.content)
        elif event.event_type == "context_compacted":
            await stream.write("\n\n*较早对话已整理为简短工作摘要。*\n")
        self.call_later(self._scroll_end)

    async def _confirm_tool(self, message: ConfirmationRequestedMessage) -> bool:
        if self._confirm_pending is not None:
            return False
        future = asyncio.get_running_loop().create_future()
        self._confirm_pending = future
        details = ", ".join(
            f"{key}={value}" for key, value in list(message.arguments.items())[:4]
        )
        self.call_later(
            self._mount_confirmation,
            message.tool_name,
            details,
            message.reason,
        )
        try:
            return await asyncio.wait_for(future, timeout=_CONFIRM_TIMEOUT)
        except (TimeoutError, asyncio.CancelledError):
            return False
        finally:
            self._confirm_pending = None

    def _mount_confirmation(self, tool: str, details: str, reason: str) -> None:
        self.query_one("#chat-log", VerticalScroll).mount(
            CommandOutput(
                f"**⚠️ Confirmation required: {tool}**\n\n"
                f"`{details}`\n\nReason: `{reason}`\n\n"
                "Type `/confirm` or `/deny`."
            )
        )
        self._scroll_end()

    async def _handle_command(self, command: str) -> bool:
        cmd = command.lower().strip()
        log = self.query_one("#chat-log", VerticalScroll)
        if cmd in {"/exit", "/quit"}:
            self.exit()
        elif cmd == "/help":
            await log.mount(CommandOutput(_HELP))
        elif cmd == "/new":
            self._session_handle = await self._client.create_session()
            self._state.clear_messages()
            await log.remove_children()
            await log.mount(CommandOutput("*Started a new conversation.*"))
        elif cmd == "/tools":
            tools = (await self._client.list_tools(self._session_handle)).tools
            rows = "\n".join(f"| `{name}` |" for name in tools)
            await log.mount(CommandOutput(f"| Tool |\n|------|\n{rows}"))
        elif cmd == "/history":
            messages = (await self._client.history(self._session_handle)).messages
            await log.mount(CommandOutput(self._history_table(messages)))
        elif cmd == "/status":
            status = await self._client.status(self._session_handle)
            rows = "\n".join(
                f"| {key} | {value} |"
                for key, value in status.model_dump(mode="json").items()
            )
            await log.mount(CommandOutput(f"| Property | Value |\n|---|---|\n{rows}"))
        elif cmd == "/memory clear":
            await self._client.clear_memory(self._session_handle)
            await log.mount(CommandOutput("*All memories cleared.*"))
        elif cmd == "/memory":
            memories = (await self._client.list_memory(self._session_handle)).memories
            rows = "\n".join(
                f"| {item.category} | {item.key} | {item.value[:60]} |"
                for item in memories
            )
            await log.mount(CommandOutput(f"| Category | Key | Value |\n|---|---|---|\n{rows}"))
        elif cmd.startswith("/config set "):
            assignment = command.strip().split(None, 2)[-1]
            if "=" not in assignment:
                await log.mount(CommandOutput(f"{ICON_WARN} Usage: `/config set key=value`"))
            else:
                field_name, value = (part.strip() for part in assignment.split("=", 1))
                result = await self._client.set_config(
                    self._session_handle,
                    field_name,
                    value,
                )
                message = "applied; restart required" if result.applied else result.error
                await log.mount(CommandOutput(message))
        elif cmd == "/export":
            messages = (await self._client.history(self._session_handle)).messages
            target = Path(self._config.runtime_root).expanduser().resolve() / "artifacts" / f"conversation_{int(time.time())}.json"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps(messages, ensure_ascii=False, indent=2), encoding="utf-8")
            await log.mount(CommandOutput(f"Exported to `{target}`"))
        elif cmd == "/retry":
            if self._last_input:
                self._run_turn(self._last_input)
        elif cmd.startswith("/theme"):
            parts = command.split(None, 1)
            if len(parts) == 2 and set_theme(parts[1].strip()):
                self._config.ui_theme = parts[1].strip()
                self.refresh_css()
            else:
                await log.mount(CommandOutput(", ".join(AVAILABLE_THEMES)))
        elif cmd == "/confirm":
            self._resolve_confirmation(True, log)
        elif cmd == "/deny":
            self._resolve_confirmation(False, log)
        else:
            await log.mount(CommandOutput(f"{ICON_WARN} Unknown command: `{command}`"))
        self._scroll_end()
        return True

    def _resolve_confirmation(self, approved: bool, log: VerticalScroll) -> None:
        future = self._confirm_pending
        if future is None or future.done():
            log.mount(CommandOutput(f"{ICON_WARN} No pending confirmation."))
            return
        future.set_result(approved)
        log.mount(CommandOutput("Approved." if approved else "Denied."))

    def action_cancel_turn(self) -> None:
        if not self._processing:
            return
        self._cancelled = True
        asyncio.create_task(self._client.cancel_active())

    def action_copy_last(self) -> None:
        text = self._selected_text() or self._last_answer
        if text:
            self._copy_worker(text)

    @work(exclusive=False)
    async def _copy_worker(self, text: str) -> None:
        self.copy_to_clipboard(text)
        ok, detail = await copy_or_save(text)
        self.notify(
            detail,
            title="Copy",
            severity="information" if ok else "warning",
        )

    def _set_status(self, text: str) -> None:
        self.query_one("#status-bar", Static).update(f" {ICON_READY} {text}")

    def _scroll_end(self) -> None:
        self.query_one("#chat-log", VerticalScroll).scroll_end(animate=False)

    @classmethod
    def _history_table(cls, messages: tuple[dict[str, Any], ...]) -> str:
        if not messages:
            return "*No conversation history.*"
        rows = []
        for index, message in enumerate(messages, start=1):
            content = cls._format_content(message.get("content", ""))[:120]
            rows.append(f"| {index} | {message.get('role', '?')} | {content} |")
        return "| # | Role | Content |\n|---|---|---|\n" + "\n".join(rows)

    @staticmethod
    def _format_content(content: Any) -> str:
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            return "".join(
                str(block.get("text", "[image]"))
                if isinstance(block, dict)
                else str(block)
                for block in content
            )
        return str(content)
