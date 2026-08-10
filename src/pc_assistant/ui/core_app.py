"""Textual client for the strict Core API."""
from __future__ import annotations

import asyncio
import json
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from textual import work
from textual.app import App, ComposeResult
from textual.containers import Vertical, VerticalScroll
from textual.widgets import Header, Markdown, Static

from pc_assistant.artifacts.delivery import save_download
from pc_assistant.branding import ASSISTANT_NAME
from pc_assistant.config import AppConfig
from pc_assistant.conversation import ChatTurnState, TERMINAL_CHAT_TURN_STATES
from pc_assistant.service.core_api import (
    ChatApprovalSnapshot,
    ChatTimelineEntrySnapshot,
    ChatTurnSnapshot,
)
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
_WELCOME = f"""\
# {ASSISTANT_NAME}

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


@dataclass
class _TurnRenderState:
    revision: int = 0
    reasoning: str = ""
    content: str = ""
    timeline_count: int = 0
    artifact_ids: set[str] = field(default_factory=set)
    tool_panels: dict[str, ToolCallPanel] = field(default_factory=dict)
    terminal_recorded: bool = False
    resolved_approval_ids: set[str] = field(default_factory=set)


class CoreChatApp(App):
    CSS_PATH = "chat.tcss"
    TITLE = ASSISTANT_NAME
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
        self._last_input = ""
        self._last_answer = ""
        self._confirm_pending: asyncio.Future[bool] | None = None
        self._active_turn_id = ""
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

    def on_unmount(self) -> None:
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
        self._set_status("thinking…")
        log = self.query_one("#chat-log", VerticalScroll)
        await log.mount(UserMessage(text))
        self._state.add_message(MessageType.USER, text)
        response = AssistantMessage()
        await log.mount(response)
        stream = Markdown.get_stream(response.markdown)
        render_state = _TurnRenderState()
        try:
            turn = await self._client.create_chat_turn(
                self._session_handle,
                text,
                client_request_id=str(uuid.uuid4()),
            )
            self._active_turn_id = turn.turn_id
            await self._render_chat_snapshot(turn, response, stream, render_state)
            await self._resolve_pending_chat_approval(turn, render_state)
            if turn.state not in TERMINAL_CHAT_TURN_STATES:
                async for snapshot in self._client.chat_turn_updates(turn.turn_id):
                    await self._render_chat_snapshot(
                        snapshot,
                        response,
                        stream,
                        render_state,
                    )
                    await self._resolve_pending_chat_approval(snapshot, render_state)
        except Exception as exc:
            await stream.write(f"\n\n{ICON_ERROR} **Error:** {exc}\n")
            self._state.add_message(MessageType.ERROR, str(exc))
        finally:
            await stream.stop()
            self._active_turn_id = ""
            self._processing = False
            self._set_status("Ready")
            log.scroll_end(animate=False)

    async def _render_chat_snapshot(
        self,
        snapshot: ChatTurnSnapshot,
        response: AssistantMessage,
        stream: Any,
        state: _TurnRenderState,
    ) -> None:
        if snapshot.revision <= state.revision:
            return
        state.revision = snapshot.revision

        reasoning_delta = self._appended_text(state.reasoning, snapshot.reasoning)
        if reasoning_delta:
            response.add_thinking(reasoning_delta)
            self._set_status("thinking…")
        state.reasoning = snapshot.reasoning

        content_delta = self._appended_text(state.content, snapshot.content)
        if content_delta:
            await stream.write(content_delta)
            self._set_status("generating…")
        state.content = snapshot.content

        for entry in snapshot.timeline[state.timeline_count:]:
            self._render_timeline_entry(response, state, entry)
        state.timeline_count = len(snapshot.timeline)

        for artifact in snapshot.artifacts:
            if artifact.artifact_id in state.artifact_ids:
                continue
            state.artifact_ids.add(artifact.artifact_id)
            try:
                downloaded = await self._client.download_artifact(
                    self._session_handle,
                    artifact.artifact_id,
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
                    f"\n\n*Artifact: `{artifact.name}` saved to `{target}`*\n"
                )

        if snapshot.state is ChatTurnState.WAITING_APPROVAL:
            self._set_status("confirmation required")
        elif snapshot.state is ChatTurnState.CANCELLED:
            await stream.write("\n\n*Cancelled.*\n")
        elif snapshot.state is ChatTurnState.FAILED:
            detail = snapshot.failure_code or "The turn did not complete"
            await stream.write(f"\n\n{ICON_ERROR} **Error:** {detail}\n")
            self._state.add_message(MessageType.ERROR, detail)

        if snapshot.state in TERMINAL_CHAT_TURN_STATES and not state.terminal_recorded:
            state.terminal_recorded = True
            final_answer = (snapshot.final_output or snapshot.content).strip()
            final_delta = self._appended_text(snapshot.content, snapshot.final_output)
            if final_delta:
                await stream.write(final_delta)
            self._last_answer = final_answer
            if final_answer:
                self._state.add_message(MessageType.ASSISTANT, final_answer)
        self.call_later(self._scroll_end)

    def _render_timeline_entry(
        self,
        response: AssistantMessage,
        state: _TurnRenderState,
        entry: ChatTimelineEntrySnapshot,
    ) -> None:
        if entry.kind == "tool_call":
            panel = response.add_tool_call(
                entry.tool_name,
                entry.tool_args,
                blocked=entry.blocked,
            )
            state.tool_panels[entry.tool_call_id] = panel
            self._state.add_message(
                MessageType.TOOL_CALL,
                f"[{entry.tool_name}]",
                tool_name=entry.tool_name,
                tool_args=entry.tool_args,
            )
            self._set_status(entry.tool_name or "using tool…")
        elif entry.kind == "tool_result":
            result = entry.tool_result
            rendered = json.dumps(result, ensure_ascii=False) if isinstance(result, dict) else str(result)
            panel = state.tool_panels.pop(entry.tool_call_id, None)
            if panel is not None:
                panel.set_result(
                    rendered,
                    is_error=entry.blocked,
                )
            self._state.add_message(
                MessageType.TOOL_RESULT,
                rendered[:200],
                tool_name=entry.tool_name,
            )

    async def _resolve_pending_chat_approval(
        self,
        snapshot: ChatTurnSnapshot,
        state: _TurnRenderState,
    ) -> None:
        approval = next(
            (
                item
                for item in snapshot.approvals
                if item.state == "pending"
                and item.approval_id not in state.resolved_approval_ids
            ),
            None,
        )
        if approval is None:
            return
        state.resolved_approval_ids.add(approval.approval_id)
        approved = await self._confirm_chat_approval(approval)
        await self._client.resolve_chat_approval(
            approval.approval_id,
            approved=approved,
        )

    async def _confirm_chat_approval(self, approval: ChatApprovalSnapshot) -> bool:
        if self._confirm_pending is not None:
            return False
        future = asyncio.get_running_loop().create_future()
        self._confirm_pending = future
        details = ", ".join(
            f"{key}={value}" for key, value in list(approval.arguments.items())[:4]
        )
        self.call_later(
            self._mount_confirmation,
            approval.tool_name,
            details,
            approval.reason,
        )
        try:
            return await asyncio.wait_for(future, timeout=_CONFIRM_TIMEOUT)
        except (TimeoutError, asyncio.CancelledError):
            return False
        finally:
            self._confirm_pending = None

    @staticmethod
    def _appended_text(previous: str, current: str) -> str:
        if not current or current == previous:
            return ""
        if current.startswith(previous):
            return current[len(previous):]
        return current if not previous else ""

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
        if not self._processing or not self._active_turn_id:
            return
        self._set_status("stopping…")
        asyncio.create_task(self._client.cancel_chat_turn(self._active_turn_id))

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
