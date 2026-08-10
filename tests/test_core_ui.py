from __future__ import annotations

import pytest

from pc_assistant.config import AppConfig
from pc_assistant.conversation import ChatTurnState
from pc_assistant.service.core_api import (
    ChatTimelineEntrySnapshot,
    ChatTurnSnapshot,
)
from pc_assistant.ui.core_app import CoreChatApp, _TurnRenderState
from pc_assistant.ui.widgets import AssistantMessage


class _Client:
    def __init__(self) -> None:
        self.sessions = 0

    async def create_session(self) -> str:
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
