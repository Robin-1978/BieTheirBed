from __future__ import annotations

import asyncio

import pytest

from pc_assistant.artifacts import ArtifactRef
from pc_assistant.config import AppConfig
from pc_assistant.tasks import TaskEvent, TaskEventPayload, TaskState
from pc_assistant.ui.core_app import CoreChatApp
from pc_assistant.ui.widgets import CommandOutput


class _Client:
    def __init__(self) -> None:
        self.handler = None
        self.sessions = 0

    def set_approval_handler(self, handler) -> None:
        self.handler = handler

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
        assert client.handler is not None
        await app._handle_command("/new")
        assert app._session_handle == "session-1"
        assert app.query_one("#chat-log") is not None

    assert client.handler is None


@pytest.mark.asyncio
async def test_core_chat_confirmation_resolves_pending_future() -> None:
    client = _Client()
    app = CoreChatApp(AppConfig(), client, "session-a")
    request = TaskEvent(
        task_id="task-a",
        event_seq=3,
        occurred_at=3.0,
        event_type="approval_requested",
        payload=TaskEventPayload(
            state=TaskState.WAITING_APPROVAL,
            approval_id="approval-a",
            tool_call_id="call-a",
            tool_name="mouse",
            tool_args={"action": "click"},
            reason="desktop_control:high",
        ),
    )

    async with app.run_test():
        confirmation = asyncio.create_task(app._confirm_tool(request))
        while app._confirm_pending is None:
            await asyncio.sleep(0)
        await app._handle_command("/confirm")

        assert await confirmation is True
        assert len(app.query(CommandOutput)) >= 2


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

    event = TaskEvent(
        task_id="task-a",
        event_seq=1,
        occurred_at=1.0,
        event_type="artifact",
        payload=TaskEventPayload(
            artifact=ArtifactRef(
                artifact_id="artifact-a",
                kind="image",
                name="capture.png",
                media_type="image/png",
                size=1,
            )
        ),
    )

    async with app.run_test():
        await app._handle_event(event, None, Stream(), [])

    assert "Artifact download failed: disk unavailable" in "".join(writes)
