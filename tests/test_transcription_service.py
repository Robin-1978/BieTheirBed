from __future__ import annotations

import base64
from typing import Any

import pytest

from pc_assistant.agent_runtime.contracts import (
    ArtifactTranscriptionRequest,
    RuntimeScope,
)
from pc_assistant.agent_runtime.tool_step import ToolArgumentPolicy, ToolStep
from pc_assistant.agent_runtime.transcription_service import (
    ArtifactTranscriptionService,
    InvalidAudioArtifactError,
    TranscriptionUnavailableError,
)
from pc_assistant.artifacts import ArtifactStore
from pc_assistant.config import AudioTranscriptionConfig
from pc_assistant.tools.base import (
    ToolBase,
    ToolCapability,
    ToolEffect,
    ToolOrigin,
    ToolOriginKind,
    ToolRisk,
)
from pc_assistant.tools.registry import ToolRegistry


_TOOL_NAME = "mcp__speech__transcribe"


class TranscriptionTool(ToolBase):
    name = _TOOL_NAME
    description = "Transcribe owned audio bytes"
    effect = ToolEffect.READ_ONLY
    capabilities = frozenset({ToolCapability.MCP, ToolCapability.NETWORK})
    risk = ToolRisk.LOW

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def execute(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        return {"structured_content": {"transcript": "明天下午三点提醒我开会"}}

    def definition(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": {
                "type": "object",
                "properties": {
                    "audio_data_url": {"type": "string"},
                    "media_type": {"type": "string"},
                    "file_name": {"type": "string"},
                },
                "required": ["audio_data_url", "media_type", "file_name"],
                "additionalProperties": False,
            },
        }


def _service(tmp_path, *, enabled: bool = True):
    store = ArtifactStore(tmp_path / "attachments", db_path=tmp_path / "data.db")
    registry = ToolRegistry()
    tool = TranscriptionTool()
    registry.register(
        tool,
        origin=ToolOrigin(ToolOriginKind.MCP, "mcp:speech"),
    )
    service = ArtifactTranscriptionService(
        AudioTranscriptionConfig(enabled=enabled, tool=_TOOL_NAME if enabled else ""),
        store,
        registry,
        ToolStep(registry, ToolArgumentPolicy(tmp_path)),
        capabilities_for=lambda _scope: frozenset(ToolCapability),
    )
    return service, store, tool


@pytest.mark.asyncio
async def test_transcription_uses_owned_audio_without_exposing_path(tmp_path) -> None:
    service, store, tool = _service(tmp_path)
    raw = b"OggSvoice"
    ref = store.put_data_url(
        "session-a",
        "data:audio/ogg;base64," + base64.b64encode(raw).decode("ascii"),
        media_type="audio/ogg",
        name="voice.ogg",
    )

    result = await service.transcribe(
        RuntimeScope(principal_id="personal:user", session_handle="session-a"),
        ArtifactTranscriptionRequest(artifact_id=ref["artifact_id"]),
    )

    assert result.transcript == "明天下午三点提醒我开会"
    assert result.tool_name == _TOOL_NAME
    assert tool.calls == [
        {
            "audio_data_url": "data:audio/ogg;base64,"
            + base64.b64encode(raw).decode("ascii"),
            "media_type": "audio/ogg",
            "file_name": "voice.ogg",
        }
    ]
    assert "path" not in tool.calls[0]


@pytest.mark.asyncio
async def test_transcription_fails_closed_when_not_configured(tmp_path) -> None:
    service, store, _tool = _service(tmp_path, enabled=False)
    ref = store.put_data_url(
        "session-a",
        "data:audio/ogg;base64,T2dnUw==",
        media_type="audio/ogg",
        name="voice.ogg",
    )

    with pytest.raises(TranscriptionUnavailableError):
        await service.transcribe(
            RuntimeScope(principal_id="personal:user", session_handle="session-a"),
            ArtifactTranscriptionRequest(artifact_id=ref["artifact_id"]),
        )


@pytest.mark.asyncio
async def test_transcription_rejects_non_audio_artifact(tmp_path) -> None:
    service, store, _tool = _service(tmp_path)
    ref = store.put_data_url(
        "session-a",
        "data:text/plain;base64,aGVsbG8=",
        media_type="text/plain",
        name="notes.txt",
    )

    with pytest.raises(InvalidAudioArtifactError):
        await service.transcribe(
            RuntimeScope(principal_id="personal:user", session_handle="session-a"),
            ArtifactTranscriptionRequest(artifact_id=ref["artifact_id"]),
        )
