"""Tests for the chat TUI (Textual rewrite) and backward-compatible ChatUI."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from pc_assistant.config import AppConfig
from pc_assistant.agent import Agent, AgentEvent
from pc_assistant.ui.chat import ICON_PROMPT, ChatUI
from pc_assistant.ui.state import UIState, Message, MessageType, AppStatus


# ── Data model tests (unchanged) ──────────────────────────────────────


class TestMessage:
    def test_id_property(self):
        msg = Message(type=MessageType.USER, content="hello world")
        msg_id = msg.id
        assert isinstance(msg_id, str)
        assert len(msg_id) > 0

    def test_default_tool_name_none(self):
        msg = Message(type=MessageType.TOOL_CALL, content="[grep]")
        assert msg.tool_name is None

    def test_default_tool_result_none(self):
        msg = Message(type=MessageType.TOOL_RESULT, content="found")
        assert msg.tool_result is None


class TestUIState:
    def test_add_message(self):
        state = UIState()
        msg = state.add_message(MessageType.USER, "hello")
        assert len(state.messages) == 1
        assert msg.type == MessageType.USER
        assert msg.content == "hello"

    def test_deque_maxlen(self):
        state = UIState()
        for i in range(2500):
            state.add_message(MessageType.USER, f"msg {i}")
        assert len(state.messages) <= 2000

    def test_clear_messages(self):
        state = UIState()
        state.add_message(MessageType.USER, "hello")
        state.add_message(MessageType.ASSISTANT, "hi")
        assert len(state.messages) == 2
        state.clear_messages()
        assert len(state.messages) == 0

    def test_debug_mode_default_false(self):
        state = UIState()
        assert state.debug_mode is False


class TestAppStatus:
    def test_token_str_thousands(self):
        status = AppStatus()
        status.total_tokens = 15000
        assert "k" in status.token_str

    def test_token_str_millions(self):
        status = AppStatus()
        status.total_tokens = 2_500_000
        assert "M" in status.token_str

    def test_token_str_small(self):
        status = AppStatus()
        status.total_tokens = 500
        assert status.token_str == "500"


# ── ChatUI init tests ─────────────────────────────────────────────────


class TestChatUIInit:
    def test_init(self):
        ui = ChatUI(config=AppConfig())
        assert ui._config is not None
        assert ui._agent is None
        assert ui._running is False

    def test_init_with_confirm_callback(self):
        def cb(t, d):
            return True
        ui = ChatUI(config=AppConfig(), confirm_callback=cb)
        assert ui._confirm_callback is cb

    def test_state_initialized(self):
        ui = ChatUI(config=AppConfig())
        assert isinstance(ui._state, UIState)


# ── ChatUI commands (console mode) ────────────────────────────────────


class TestChatUICommands:
    @pytest.mark.asyncio
    async def test_exit_command(self):
        ui = ChatUI(config=AppConfig())
        ui._running = True
        result = await ui._handle_user_command("/exit")
        assert result is True
        assert ui._running is False

    @pytest.mark.asyncio
    async def test_quit_command(self):
        ui = ChatUI(config=AppConfig())
        ui._running = True
        result = await ui._handle_user_command("/quit")
        assert result is True
        assert ui._running is False

    @pytest.mark.asyncio
    async def test_clear_command(self):
        ui = ChatUI(config=AppConfig())
        agent = Agent(config=AppConfig())
        ui._agent = agent
        result = await ui._handle_user_command("/clear")
        assert result is True

    @pytest.mark.asyncio
    async def test_help_command(self):
        ui = ChatUI(config=AppConfig())
        result = await ui._handle_user_command("/help")
        assert result is True

    @pytest.mark.asyncio
    async def test_config_command(self):
        ui = ChatUI(config=AppConfig())
        result = await ui._handle_user_command("/config")
        assert result is True

    @pytest.mark.asyncio
    async def test_tools_no_agent(self):
        ui = ChatUI(config=AppConfig())
        result = await ui._handle_user_command("/tools")
        assert result is True

    @pytest.mark.asyncio
    async def test_history_no_agent(self):
        ui = ChatUI(config=AppConfig())
        result = await ui._handle_user_command("/history")
        assert result is True

    @pytest.mark.asyncio
    async def test_unknown_command(self):
        ui = ChatUI(config=AppConfig())
        result = await ui._handle_user_command("/unknown")
        assert result is True

    @pytest.mark.asyncio
    async def test_debug_command(self):
        ui = ChatUI(config=AppConfig())
        initial = ui._state.debug_mode
        result = await ui._handle_user_command("/debug")
        assert result is True
        assert ui._state.debug_mode != initial


# ── ChatUI event processing (console mode) ────────────────────────────


class TestChatUIProcessEvents:
    @pytest.mark.asyncio
    async def test_process_events_no_agent(self):
        ui = ChatUI(config=AppConfig())
        await ui._process_events("hello")
        error_msgs = [m for m in ui._state.messages if m.type == MessageType.ERROR]
        assert len(error_msgs) > 0
        assert "not initialized" in error_msgs[0].content.lower()

    @pytest.mark.asyncio
    async def test_process_events_creates_assistant_message(self, capsys):
        ui = ChatUI(config=AppConfig())
        agent = Agent(config=AppConfig())

        async def _run_with_delta(*args, **kwargs):
            yield AgentEvent(type="stream_start", content="")
            yield AgentEvent(type="stream_delta", content="Hello world")
            yield AgentEvent(type="stream_end", content="")

        agent.run = _run_with_delta
        ui._agent = agent
        await ui._process_events("hello")
        captured = capsys.readouterr()
        assert "Hello world" in captured.out

    @pytest.mark.asyncio
    async def test_process_events_think_message(self, capsys):
        ui = ChatUI(config=AppConfig())
        agent = Agent(config=AppConfig())

        async def _run_with_think(*args, **kwargs):
            yield AgentEvent(type="stream_start", content="")
            yield AgentEvent(type="stream_think_delta", content="hmm")
            yield AgentEvent(type="stream_delta", content="Answer")
            yield AgentEvent(type="stream_end", content="")

        agent.run = _run_with_think
        ui._agent = agent
        await ui._process_events("hello")
        captured = capsys.readouterr()
        assert "Answer" in captured.out
        assert "hmm" in captured.out

    @pytest.mark.asyncio
    async def test_process_events_cancel_protection(self):
        ui = ChatUI(config=AppConfig())
        agent = Agent(config=AppConfig())

        async def _run_with_cancel(*args, **kwargs):
            yield AgentEvent(type="stream_start", content="")
            ui._cancel()
            yield AgentEvent(type="stream_delta", content="Hello")

        agent.run = _run_with_cancel
        ui._agent = agent
        await ui._process_events("hello")
        assert ui._cancelled is True

    @pytest.mark.asyncio
    async def test_process_events_processing_false_after(self):
        ui = ChatUI(config=AppConfig())
        agent = Agent(config=AppConfig())

        async def _run_simple(*args, **kwargs):
            yield AgentEvent(type="stream_start", content="")
            yield AgentEvent(type="final_answer", content="Done")

        agent.run = _run_simple
        ui._agent = agent
        await ui._process_events("hello")
        assert ui._state.processing is False

    @pytest.mark.asyncio
    async def test_process_events_tool_call(self):
        ui = ChatUI(config=AppConfig())
        agent = Agent(config=AppConfig())

        async def _run_with_tool(*args, **kwargs):
            yield AgentEvent(type="stream_start", content="")
            yield AgentEvent(type="tool_call", content="", tool_name="grep", tool_args={"pattern": "test"})
            yield AgentEvent(type="tool_result", content="found", tool_name="grep", tool_result="found")
            yield AgentEvent(type="stream_delta", content="Done")
            yield AgentEvent(type="stream_end", content="")

        agent.run = _run_with_tool
        ui._agent = agent
        await ui._process_events("hello")
        tool_msgs = [m for m in ui._state.messages if m.type == MessageType.TOOL_CALL]
        assert len(tool_msgs) >= 1
        assert tool_msgs[0].tool_name == "grep"

    @pytest.mark.asyncio
    async def test_final_answer_recorded(self):
        ui = ChatUI(config=AppConfig())
        agent = Agent(config=AppConfig())

        async def _run_ok(*args, **kwargs):
            yield AgentEvent(type="stream_start", content="")
            yield AgentEvent(type="stream_delta", content="hi there")
            yield AgentEvent(type="stream_end", content="")
            yield AgentEvent(type="final_answer", content="hi there")

        agent.run = _run_ok
        ui._agent = agent
        await ui._process_events("hello")

        assert ui._state.processing is False
        assistant_msgs = [m for m in ui._state.messages if m.type == MessageType.ASSISTANT]
        assert len(assistant_msgs) >= 1
        assert "hi there" in assistant_msgs[0].content


# ── ChatUI cancel ─────────────────────────────────────────────────────


class TestChatUICancel:
    def test_cancel_sets_flag(self):
        ui = ChatUI(config=AppConfig())
        agent = Agent(config=AppConfig())
        ui._agent = agent
        ui._cancel()
        assert ui._cancelled is True

    def test_cancel_calls_agent_cancel(self):
        ui = ChatUI(config=AppConfig())
        agent = Agent(config=AppConfig())
        ui._agent = agent
        ui._cancel()
        assert agent._cancelled is True

    def test_cancel_no_double(self):
        ui = ChatUI(config=AppConfig())
        agent = Agent(config=AppConfig())
        ui._agent = agent
        ui._cancel()
        ui._cancel()
        assert ui._cancelled is True


# ── ChatUI welcome (console mode) ─────────────────────────────────────


class TestChatUIShowWelcome:
    def test_show_welcome(self, capsys):
        ui = ChatUI(config=AppConfig())
        ui._show_welcome()
        captured = capsys.readouterr()
        assert "help" in captured.out.lower()


# ── Textual widget tests ──────────────────────────────────────────────


class TestWidgets:
    def test_user_message_stores_text(self):
        from pc_assistant.ui.widgets import UserMessage
        msg = UserMessage("hello world")
        assert msg._text == "hello world"

    def test_assistant_message_init(self):
        from pc_assistant.ui.widgets import AssistantMessage
        msg = AssistantMessage()
        assert msg._thinking is None
        assert msg._md is None

    def test_thinking_panel_append(self):
        from pc_assistant.ui.widgets import ThinkingPanel
        panel = ThinkingPanel()
        panel._chunks = []
        panel.append("chunk1")
        panel.append("chunk2")
        assert panel._chunks == ["chunk1", "chunk2"]

    def test_tool_call_panel_header_format(self):
        from pc_assistant.ui.widgets import ToolCallPanel
        panel = ToolCallPanel("grep", {"pattern": "test"})
        header = panel._format_header()
        assert "grep" in header
        assert "pattern" in header

    def test_tool_call_panel_blocked_format(self):
        from pc_assistant.ui.widgets import ToolCallPanel
        panel = ToolCallPanel("rm", {}, blocked=True, block_reason="dangerous")
        header = panel._format_header()
        assert "Blocked" in header
        assert "dangerous" in header

    def test_tool_call_panel_no_args(self):
        from pc_assistant.ui.widgets import ToolCallPanel
        panel = ToolCallPanel("list_tools", {})
        header = panel._format_header()
        assert "list_tools" in header

    def test_chat_input_history_init(self):
        from pc_assistant.ui.widgets import ChatInput
        inp = ChatInput()
        assert inp._history == []
        assert inp._history_idx == -1

    def test_command_output_stores_content(self):
        from pc_assistant.ui.widgets import CommandOutput
        out = CommandOutput("hello")
        assert out._content == "hello"


# ── ChatApp tests (async Textual pilot) ───────────────────────────────


class TestChatApp:
    @pytest.mark.asyncio
    async def test_app_creates_and_shows_welcome(self):
        from pc_assistant.ui.app import ChatApp
        app = ChatApp(config=AppConfig())
        async with app.run_test() as pilot:
            log = app.query_one("#chat-log")
            assert log is not None
            status = app.query_one("#status-bar")
            assert status is not None
            inp = app.query_one("#user-input")
            assert inp is not None

    @pytest.mark.asyncio
    async def test_app_slash_help(self):
        from pc_assistant.ui.app import ChatApp
        from pc_assistant.ui.widgets import CommandOutput
        app = ChatApp(config=AppConfig())
        async with app.run_test() as pilot:
            await app._handle_command("/help")
            outputs = app.query(CommandOutput)
            assert len(outputs) >= 2  # welcome + help

    @pytest.mark.asyncio
    async def test_app_slash_config(self):
        from pc_assistant.ui.app import ChatApp
        from pc_assistant.ui.widgets import CommandOutput
        app = ChatApp(config=AppConfig())
        async with app.run_test() as pilot:
            await app._handle_command("/config")
            outputs = app.query(CommandOutput)
            assert len(outputs) >= 2

    @pytest.mark.asyncio
    async def test_app_unknown_command(self):
        from pc_assistant.ui.app import ChatApp
        from pc_assistant.ui.widgets import CommandOutput
        app = ChatApp(config=AppConfig())
        async with app.run_test() as pilot:
            await app._handle_command("/foobar")
            outputs = app.query(CommandOutput)
            assert len(outputs) >= 2

    @pytest.mark.asyncio
    async def test_app_clear_command(self):
        from pc_assistant.ui.app import ChatApp
        app = ChatApp(config=AppConfig())
        async with app.run_test() as pilot:
            await app._handle_command("/clear")
            log = app.query_one("#chat-log")
            assert log is not None

    @pytest.mark.asyncio
    async def test_app_cancel_when_not_processing(self):
        from pc_assistant.ui.app import ChatApp
        app = ChatApp(config=AppConfig())
        async with app.run_test() as pilot:
            app._processing = False
            app.action_cancel_turn()

    @pytest.mark.asyncio
    async def test_app_debug_toggle(self):
        from pc_assistant.ui.app import ChatApp
        app = ChatApp(config=AppConfig())
        async with app.run_test() as pilot:
            initial = app._state.debug_mode
            await app._handle_command("/debug")
            assert app._state.debug_mode != initial
