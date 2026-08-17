from __future__ import annotations

from types import SimpleNamespace

import pytest

from knoa_platform.config import AppConfig
from knoa_platform.conversation import ChatTurnState
from knoa_platform.service.core_api import (
    ChatTimelineEntrySnapshot,
    ChatTurnSnapshot,
)
from knoa_platform.ui.core_app import CoreChatApp, _TurnRenderState
from knoa_platform.ui.widgets import AssistantMessage


class _Client:
    def __init__(self) -> None:
        self.sessions = 0

    async def create_session(self, *, agent_id=None) -> str:
        del agent_id
        self.sessions += 1
        return f"session-{self.sessions}"

    async def download_artifact(self, session_handle, artifact_id):
        del session_handle, artifact_id
        raise OSError("disk unavailable")


@pytest.mark.asyncio
async def test_core_chat_mounts_and_creates_core_owned_session() -> None:
    client = _Client()
    app = CoreChatApp(AppConfig(), client, "session-initial")

    async with app.run_test():
        await app._handle_command("/new")
        assert app._session_handle == "session-1"
        assert app.query_one("#chat-log") is not None


@pytest.mark.asyncio
async def test_artifact_download_failure_is_a_local_warning_and_does_not_raise(
    tmp_path,
) -> None:
    client = _Client()
    app = CoreChatApp(
        AppConfig(runtime_root=str(tmp_path)),
        client,
        "session-a",
    )
    writes = []

    class Stream:
        async def write(self, value):
            writes.append(value)

    async with app.run_test():
        response = AssistantMessage()
        await app.query_one("#chat-log").mount(response)
        await app._render_chat_snapshot(
            _snapshot(
                artifacts=({
                    "artifact_id": "artifact-a",
                    "kind": "image",
                    "name": "capture.png",
                    "media_type": "image/png",
                    "size": 1,
                },),
            ),
            response,
            Stream(),
            _TurnRenderState(),
        )

    assert "Artifact download failed: disk unavailable" in "".join(writes)


@pytest.mark.asyncio
async def test_chat_snapshot_renders_reasoning_tools_and_authoritative_answer() -> None:
    client = _Client()
    app = CoreChatApp(AppConfig(), client, "session-a")
    writes = []

    class Stream:
        async def write(self, value):
            writes.append(value)

    async with app.run_test():
        response = AssistantMessage()
        await app.query_one("#chat-log").mount(response)
        await app._render_chat_snapshot(
            _snapshot(
                state=ChatTurnState.COMPLETED,
                reasoning="Checking context.",
                content="Hello from Xiao Nuo.",
                final_output="Hello from Xiao Nuo.",
                timeline=(
                    ChatTimelineEntrySnapshot(
                        kind="tool_call",
                        tool_call_id="call-a",
                        tool_name="status",
                        tool_args={},
                    ),
                    ChatTimelineEntrySnapshot(
                        kind="tool_result",
                        tool_call_id="call-a",
                        tool_name="status",
                        tool_result={"ok": True},
                    ),
                ),
            ),
            response,
            Stream(),
            _TurnRenderState(),
        )

    assert writes == ["Hello from Xiao Nuo."]
    assert app._last_answer == "Hello from Xiao Nuo."
    assert app._state.messages[-1].content == "Hello from Xiao Nuo."
    assert any(message.type.value == "tool_call" for message in app._state.messages)


@pytest.mark.asyncio
async def test_tui_can_select_agent_and_resolve_interaction() -> None:
    class Client(_Client):
        def __init__(self):
            super().__init__()
            self.resolved = None

        async def resolve_interaction(self, interaction_id, value):
            self.resolved = (interaction_id, value)

    base = AppConfig()
    config = AppConfig(
        default_agent="codex",
        node_agents={
            **base.node_agents,
            "codex": base.node_agents["codex"].model_copy(
                update={"enabled": True}
            ),
        },
    )
    client = Client()
    app = CoreChatApp(config, client, "session-a")

    async with app.run_test():
        await app._handle_command("/agent knoa")
        await app._handle_command('/resolve interaction-a {"action":"decline"}')

    assert app._agent_id == "knoa"
    assert client.resolved == ("interaction-a", {"action": "decline"})


def test_task_execution_markdown_shows_full_approval_arguments() -> None:
    execution = SimpleNamespace(
        execution_id="execution-a",
        task_id="task-a",
        agent_id_snapshot="knoa",
        state="waiting_approval",
        final_result="",
        failure_code="",
        phase="",
        interactions=(),
        approvals=(
            SimpleNamespace(
                approval_id="approval-a",
                tool_name="run_command",
                arguments={"command": "pwd && find /tmp -name gs_map"},
                reason="local_write:high",
                state="pending",
            ),
        ),
    )

    rendered = CoreChatApp._execution_markdown(execution)

    assert "run_command" in rendered
    assert "local_write:high" in rendered
    assert "pwd && find /tmp -name gs_map" in rendered


def _snapshot(**updates) -> ChatTurnSnapshot:
    data = {
        "turn_id": "turn-a",
        "session_handle": "session-a",
        "client_request_id": "request-a",
        "user_input": "hello",
        "attachments": (),
        "tools_enabled": True,
        "state": ChatTurnState.RUNNING,
        "reasoning": "",
        "content": "",
        "final_output": "",
        "artifacts": (),
        "failure_code": "",
        "cancel_requested": False,
        "tool_steps": (),
        "approvals": (),
        "timeline": (),
        "created_at": 1.0,
        "updated_at": 1.0,
        "finished_at": None,
        "revision": 1,
    }
    data.update(updates)
    return ChatTurnSnapshot.model_validate(data)
