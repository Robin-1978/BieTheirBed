from __future__ import annotations

import base64

import pytest

from knoa_platform.agent_runtime.contracts import (
    ArtifactDownloadResult,
    HealthStatus,
)
from knoa_platform.artifacts import ArtifactRef
from knoa_platform.cli_core import run_core_ask
from knoa_platform.config import AppConfig
from knoa_platform.tasks import TaskEvent, TaskEventPayload, TaskState


PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGP4z8AAAAMBAQDJ/pLvAAAAAElFTkSuQmCC"
)


class _Client:
    def __init__(self) -> None:
        self.tools_enabled = None
        self.attachments = ()
        self.uploaded = ""
        self.disconnected = False
        self.download_error: Exception | None = None

    async def health(self):
        return HealthStatus(healthy=True)

    async def create_session(self):
        return "session-a"

    async def upload_artifact(self, session_handle, data_url, **kwargs):
        assert session_handle == "session-a"
        self.uploaded = data_url
        return ArtifactRef(
            artifact_id="artifact-a",
            kind="image",
            name=kwargs.get("caption", "image.png"),
            media_type=kwargs["media_type"],
            size=1,
        )

    async def download_artifact(self, session_handle, artifact_id):
        assert session_handle == "session-a"
        assert artifact_id == "capture-a"
        if self.download_error is not None:
            raise self.download_error
        encoded = base64.b64encode(PNG).decode("ascii")
        return ArtifactDownloadResult(
            artifact=ArtifactRef(
                artifact_id="capture-a",
                kind="image",
                name="capture.png",
                media_type="image/png",
                size=len(PNG),
                status="delivered",
            ),
            data_url=f"data:image/png;base64,{encoded}",
        )

    def execute_task(
        self,
        session_handle,
        user_input,
        attachments=(),
        *,
        tools_enabled=True,
    ):
        assert session_handle == "session-a"
        assert user_input == "hello"
        self.tools_enabled = tools_enabled
        self.attachments = attachments

        async def stream():
            yield TaskEvent(
                task_id="task-a",
                event_seq=1,
                occurred_at=1.0,
                event_type="task_created",
                payload=TaskEventPayload(state=TaskState.QUEUED),
            )
            yield TaskEvent(
                task_id="task-a",
                event_seq=2,
                occurred_at=2.0,
                event_type="artifact",
                payload=TaskEventPayload(
                    artifact=ArtifactRef(
                        artifact_id="capture-a",
                        kind="image",
                        name="capture.png",
                        media_type="image/png",
                        size=len(PNG),
                    )
                ),
            )
            yield TaskEvent(
                task_id="task-a",
                event_seq=3,
                occurred_at=3.0,
                event_type="completed",
                payload=TaskEventPayload(
                    state=TaskState.COMPLETED,
                    content="done",
                ),
            )

        return stream()

    async def disconnect(self):
        self.disconnected = True


class _InteractionClient(_Client):
    def __init__(self) -> None:
        super().__init__()
        self.resolution = None

    def execute_task(self, session_handle, user_input, attachments=(), *, tools_enabled=True):
        del session_handle, user_input, attachments, tools_enabled

        async def stream():
            yield TaskEvent(
                task_id="task-a",
                event_seq=1,
                occurred_at=1.0,
                event_type="interaction_requested",
                payload=TaskEventPayload(
                    interaction_id="interaction-a",
                    interaction_kind="mcp_elicitation",
                    interaction_display={"description": "Choose a transition"},
                    interaction_schema={
                        "type": "object",
                        "properties": {
                            "action": {"type": "string"},
                            "content": {"type": "object"},
                        },
                    },
                ),
            )
            yield TaskEvent(
                task_id="task-a",
                event_seq=2,
                occurred_at=2.0,
                event_type="completed",
                payload=TaskEventPayload(content="done", state=TaskState.COMPLETED),
            )

        return stream()

    async def resolve_interaction(self, interaction_id, value):
        assert interaction_id == "interaction-a"
        self.resolution = value


@pytest.mark.asyncio
async def test_core_ask_uses_strict_client_and_request_scoped_no_tools(
    tmp_path,
    monkeypatch,
    capsys,
) -> None:
    image = tmp_path / "screen.png"
    image.write_bytes(PNG)
    client = _Client()

    async def connect(config, **kwargs):
        del config, kwargs
        return client

    monkeypatch.setattr("knoa_platform.cli_core.get_core_client", connect)
    config = AppConfig(fallback_enabled=False, runtime_root=str(tmp_path))

    result = await run_core_ask(
        config,
        "hello",
        json_output=True,
        no_tools=True,
        attachments=[str(image)],
    )

    output = capsys.readouterr().out
    assert result == 0
    assert '"answer": "done"' in output
    assert client.tools_enabled is False
    assert client.attachments[0].artifact_id == "artifact-a"
    assert client.uploaded.startswith("data:image/png;base64,")
    downloads = list((tmp_path / "downloads").glob("capture-a-*"))
    assert len(downloads) == 1
    assert downloads[0].read_bytes() == PNG
    assert str(downloads[0]) in output
    assert client.disconnected


@pytest.mark.asyncio
async def test_core_ask_keeps_streaming_when_artifact_download_fails(
    tmp_path,
    monkeypatch,
    capsys,
) -> None:
    client = _Client()
    client.download_error = OSError("disk unavailable")

    async def connect(config, **kwargs):
        del config, kwargs
        return client

    monkeypatch.setattr("knoa_platform.cli_core.get_core_client", connect)
    config = AppConfig(fallback_enabled=False, runtime_root=str(tmp_path))

    result = await run_core_ask(config, "hello", json_output=True)

    output = capsys.readouterr().out
    assert result == 0
    assert '"answer": "done"' in output
    assert "Artifact download failed: disk unavailable" in output
    assert client.disconnected


@pytest.mark.asyncio
async def test_core_ask_resolves_structured_mcp_elicitation(monkeypatch) -> None:
    client = _InteractionClient()

    async def connect(config, **kwargs):
        del config, kwargs
        return client

    monkeypatch.setattr("knoa_platform.cli_core.get_core_client", connect)
    monkeypatch.setattr(
        "builtins.input",
        lambda _prompt: '{"action":"accept","content":{"transition_id":"101"}}',
    )

    result = await run_core_ask(AppConfig(fallback_enabled=False), "hello")

    assert result == 0
    assert client.resolution == {
        "action": "accept",
        "content": {"transition_id": "101"},
    }
