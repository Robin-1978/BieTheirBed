"""Owned audio Artifact transcription through one explicitly mapped MCP Tool."""
from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable

from pc_assistant.agent_runtime.contracts import (
    ArtifactTranscriptionRequest,
    ArtifactTranscriptionResult,
    RuntimeScope,
)
from pc_assistant.agent_runtime.tool_step import (
    ProposedToolCall,
    ToolStep,
    ToolStepContext,
)
from pc_assistant.artifacts import ArtifactStore
from pc_assistant.config import AudioTranscriptionConfig
from pc_assistant.tools.base import (
    ToolCapability,
    ToolEffect,
    ToolOriginKind,
    ToolRisk,
)
from pc_assistant.tools.registry import ToolRegistry


class TranscriptionUnavailableError(PermissionError):
    """Raised when no safe configured transcription capability is available."""


class InvalidAudioArtifactError(ValueError):
    """Raised when the owned Artifact is absent or is not supported audio."""


class TranscriptionFailedError(RuntimeError):
    """Raised when the mapped MCP Tool cannot produce bounded transcript text."""


class ArtifactTranscriptionService:
    """Keep transport, Artifact ownership and MCP execution as separate concerns."""

    def __init__(
        self,
        config: AudioTranscriptionConfig,
        artifacts: ArtifactStore,
        registry: ToolRegistry,
        tool_step: ToolStep,
        *,
        capabilities_for: Callable[[RuntimeScope], frozenset[ToolCapability]],
    ) -> None:
        self._config = config
        self._artifacts = artifacts
        self._registry = registry
        self._tool_step = tool_step
        self._capabilities_for = capabilities_for

    async def transcribe(
        self,
        scope: RuntimeScope,
        request: ArtifactTranscriptionRequest,
    ) -> ArtifactTranscriptionResult:
        tool_name = self._configured_tool()
        try:
            metadata = await asyncio.to_thread(
                self._artifacts.metadata,
                scope.session_handle,
                request.artifact_id,
            )
        except KeyError as exc:
            raise InvalidAudioArtifactError("Audio artifact not found") from exc
        media_type = str(metadata.get("media_type", ""))
        if not media_type.startswith("audio/"):
            raise InvalidAudioArtifactError("Artifact is not audio")
        if metadata.get("direction") != "inbound":
            raise InvalidAudioArtifactError("Only inbound audio can be transcribed")
        if int(metadata.get("size") or 0) > self._config.max_bytes:
            raise InvalidAudioArtifactError("Audio exceeds transcription limit")
        try:
            data_url = await asyncio.to_thread(
                self._artifacts.read_data_url,
                scope.session_handle,
                request.artifact_id,
                max_bytes=self._config.max_bytes,
            )
        except (KeyError, OSError, ValueError) as exc:
            raise InvalidAudioArtifactError("Audio artifact is unavailable") from exc

        call = ProposedToolCall(
            call_id=uuid.uuid4().hex,
            name=tool_name,
            arguments={
                "audio_data_url": data_url,
                "media_type": media_type,
                "file_name": str(metadata.get("name") or "audio.bin"),
            },
        )
        result = await self._tool_step.execute(
            ToolStepContext(
                scope=scope,
                run_id=f"transcription-{uuid.uuid4().hex}",
                client_request_id=call.call_id,
                capabilities=self._capabilities_for(scope),
                cancellation=asyncio.Event(),
            ),
            call,
        )
        if result.status != "completed":
            if result.code in {
                "capability_denied",
                "confirmation_required",
                "tool_not_found",
            }:
                raise TranscriptionUnavailableError("Transcription capability unavailable")
            raise TranscriptionFailedError(
                result.message or "Transcription tool failed"
            )
        transcript = self._transcript_text(result.output)
        if not transcript:
            raise TranscriptionFailedError("Transcription tool returned no text")
        if len(transcript) > 200_000:
            raise TranscriptionFailedError("Transcript exceeds Core limit")
        return ArtifactTranscriptionResult(
            artifact_id=request.artifact_id,
            transcript=transcript,
            tool_name=tool_name,
        )

    def _configured_tool(self) -> str:
        if not self._config.enabled or not self._config.tool:
            raise TranscriptionUnavailableError("Audio transcription is not configured")
        tool = self._registry.get(self._config.tool)
        origin = self._registry.origin(self._config.tool)
        if tool is None or origin is None or origin.kind is not ToolOriginKind.MCP:
            raise TranscriptionUnavailableError("Configured transcription MCP Tool is absent")
        policy = tool.policy
        if policy.effect is not ToolEffect.READ_ONLY or policy.risk is ToolRisk.HIGH:
            raise TranscriptionUnavailableError(
                "Transcription MCP Tool must be read-only and non-high-risk"
            )
        return self._config.tool

    @staticmethod
    def _transcript_text(output: object) -> str:
        if isinstance(output, str):
            return output.strip()
        if not isinstance(output, dict):
            return ""
        structured = output.get("structured_content")
        if isinstance(structured, dict):
            for key in ("transcript", "text"):
                value = structured.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
        content = output.get("content")
        if not isinstance(content, list):
            return ""
        pieces = [
            str(block.get("text", "")).strip()
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        ]
        return "\n".join(piece for piece in pieces if piece).strip()
