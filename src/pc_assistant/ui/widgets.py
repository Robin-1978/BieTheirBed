"""Custom Textual widgets for the chat TUI."""
from __future__ import annotations

import json
from typing import Any

from textual.app import ComposeResult
from textual.message import Message as TMessage
from textual.widgets import Collapsible, Markdown, Static, TextArea
from textual.widget import Widget


ICON_TOOL = "\u25cf"       # ●
ICON_SUCCESS = "\u2713"    # ✓
ICON_ERROR = "\u2717"      # ✗
ICON_WARN = "\u25b2"       # ▲
ICON_THINK = "\u25e6"      # ◦
ICON_READY = "\u25cf"      # ●


class UserMessage(Widget):
    """Displays a user message with a role label."""

    DEFAULT_CSS = "UserMessage { height: auto; }"

    def __init__(self, text: str, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._text = text

    def compose(self) -> ComposeResult:
        yield Static("You", classes="label")
        yield Static(self._text, classes="body")

    @property
    def copy_text(self) -> str:
        return self._text


class ThinkingPanel(Widget):
    """Collapsible panel showing LLM thinking/reasoning text."""

    DEFAULT_CSS = "ThinkingPanel { height: auto; }"

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._chunks: list[str] = []

    def compose(self) -> ComposeResult:
        yield Collapsible(
            Static("", id="thinking-text"),
            title=f"{ICON_THINK} Thinking\u2026",
            collapsed=True,
        )

    def append(self, text: str) -> None:
        self._chunks.append(text)
        full = "".join(self._chunks)
        try:
            widget = self.query_one("#thinking-text", Static)
            widget.update(full)
        except Exception:
            pass


class ToolCallPanel(Widget):
    """Compact display for a tool invocation and its result."""

    DEFAULT_CSS = "ToolCallPanel { height: auto; }"

    def __init__(
        self,
        tool_name: str,
        tool_args: dict[str, Any],
        *,
        blocked: bool = False,
        block_reason: str = "",
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self._tool_name = tool_name
        self._tool_args = tool_args
        self._blocked = blocked
        self._block_reason = block_reason

    def compose(self) -> ComposeResult:
        header = self._format_header()
        css_class = "tool-blocked" if self._blocked else "tool-header"
        yield Static(header, classes=css_class)

    def _format_header(self) -> str:
        if self._blocked:
            return f"{ICON_WARN} Blocked: {self._tool_name} \u2014 {self._block_reason}"
        items = list(self._tool_args.items())
        if items:
            k, v = items[0]
            val = json.dumps(v, ensure_ascii=False)
            if len(val) > 60:
                val = val[:57] + "\u2026"
            return f"{ICON_TOOL} {self._tool_name} {k}={val}"
        return f"{ICON_TOOL} {self._tool_name}"

    def set_result(self, result: str, is_error: bool = False) -> None:
        icon = ICON_ERROR if is_error else ICON_SUCCESS
        truncated = result[:200]
        if len(result) > 200:
            truncated += "\u2026"
        css_class = "tool-result-err" if is_error else "tool-result-ok"
        self.mount(Static(f"  {icon} {truncated}", classes=css_class))

    @property
    def copy_text(self) -> str:
        lines = [self._format_header()]
        for child in self.children:
            if isinstance(child, Static):
                text = str(child.renderable)
                if text:
                    lines.append(text)
        return "\n".join(lines)


class AssistantMessage(Widget):
    """Container for an assistant turn: thinking + tool calls + markdown answer."""

    DEFAULT_CSS = "AssistantMessage { height: auto; }"

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._thinking: ThinkingPanel | None = None
        self._md: Markdown | None = None

    def compose(self) -> ComposeResult:
        yield Static("Assistant", classes="label")
        self._md = Markdown("", id="response-md")
        yield self._md

    @property
    def markdown(self) -> Markdown:
        if self._md is None:
            self._md = self.query_one("#response-md", Markdown)
        return self._md

    def add_thinking(self, chunk: str) -> None:
        if self._thinking is None:
            self._thinking = ThinkingPanel()
            md = self.markdown
            self.mount(self._thinking, before=md)
        self._thinking.append(chunk)

    def add_tool_call(
        self,
        tool_name: str,
        tool_args: dict[str, Any],
        *,
        blocked: bool = False,
        block_reason: str = "",
    ) -> ToolCallPanel:
        panel = ToolCallPanel(
            tool_name, tool_args,
            blocked=blocked, block_reason=block_reason,
        )
        md = self.markdown
        self.mount(panel, before=md)
        return panel

    @property
    def copy_text(self) -> str:
        parts: list[str] = []
        if self._thinking is not None:
            parts.append("".join(self._thinking._chunks))
        for child in self.children:
            if isinstance(child, ToolCallPanel):
                parts.append(child.copy_text)
            else:
                source = getattr(child, "source", "")
                if source:
                    parts.append(str(source))
        return "\n\n".join(p for p in parts if p).strip()


class CommandOutput(Widget):
    """Renders slash-command output as markdown inside a subtle panel."""

    DEFAULT_CSS = "CommandOutput { height: auto; }"

    def __init__(self, content: str, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._content = content

    def compose(self) -> ComposeResult:
        yield Markdown(self._content)

    @property
    def copy_text(self) -> str:
        return self._content


SLASH_COMMANDS = [
    "/help", "/exit", "/quit", "/new", "/tools", "/history",
    "/status", "/config", "/config set ", "/memory", "/memory clear",
    "/retry", "/debug", "/export", "/theme", "/copy",
    "/confirm", "/deny",
]


class ChatInput(TextArea):
    """Multiline text input: Enter submits, Shift+Enter inserts newline.

    Typing ``/`` shows slash-command completions; Tab accepts the
    highlighted completion.
    """

    class Submitted(TMessage):
        """Fired when the user presses Enter to submit."""
        def __init__(self, value: str) -> None:
            super().__init__()
            self.value = value

    DEFAULT_CSS = "ChatInput { height: auto; min-height: 1; max-height: 6; }"

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(
            language=None,
            show_line_numbers=False,
            **kwargs,
        )
        self._history: list[str] = []
        self._history_idx = -1
        self._completions: list[str] = []
        self._completion_idx = -1

    async def _on_key(self, event: Any) -> None:
        if event.key == "enter":
            event.prevent_default()
            event.stop()
            text = self.text.strip()
            if text:
                self._history.append(text)
                self._history_idx = -1
                self._reset_completions()
                self.clear()
                self.post_message(self.Submitted(text))
        elif event.key == "tab" and self._completions:
            event.prevent_default()
            event.stop()
            self._completion_idx = (self._completion_idx + 1) % len(self._completions)
            self.load_text(self._completions[self._completion_idx])
        elif event.key == "shift+tab" and self._completions:
            event.prevent_default()
            event.stop()
            self._completion_idx = (self._completion_idx - 1) % len(self._completions)
            self.load_text(self._completions[self._completion_idx])
        elif event.key == "escape":
            if self._completions:
                self._reset_completions()
            elif self.text:
                self.clear()
        elif event.key == "up" and not self.text.strip():
            event.prevent_default()
            event.stop()
            if self._history:
                if self._history_idx == -1:
                    self._history_idx = len(self._history) - 1
                elif self._history_idx > 0:
                    self._history_idx -= 1
                self.load_text(self._history[self._history_idx])
        elif event.key == "down" and self._history_idx >= 0:
            event.prevent_default()
            event.stop()
            if self._history_idx < len(self._history) - 1:
                self._history_idx += 1
                self.load_text(self._history[self._history_idx])
            else:
                self._history_idx = -1
                self.clear()

    def on_text_area_changed(self, event: Any) -> None:
        text = self.text
        if text.startswith("/") and "\n" not in text:
            prefix = text.lower()
            self._completions = [c for c in SLASH_COMMANDS if c.startswith(prefix)]
            self._completion_idx = -1
        else:
            self._reset_completions()

    def _reset_completions(self) -> None:
        self._completions = []
        self._completion_idx = -1
