"""Artifact Core command handlers."""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from pc_assistant.agent_runtime.contracts import (
    ArtifactDownloadRequest,
    ArtifactServicePort,
    ArtifactTranscriptionRequest,
    ArtifactTranscriptionServicePort,
    ArtifactUploadRequest,
    RuntimeScope,
)
from pc_assistant.agent_runtime.transcription_service import (
    TranscriptionUnavailableError,
)
from pc_assistant.service.core_api import (
    ArtifactDownloadedMessage,
    ArtifactTranscribedMessage,
    ArtifactUploadedMessage,
    DownloadArtifactRequest,
    TranscribeArtifactRequest,
    UploadArtifactRequest,
)

Send = Callable[[Any], Awaitable[None]]


class ArtifactCommandHandler:
    def __init__(
        self,
        artifacts: ArtifactServicePort,
        transcription: ArtifactTranscriptionServicePort | None,
    ) -> None:
        self._artifacts = artifacts
        self._transcription = transcription

    async def dispatch(self, principal: str, request: Any, send: Send) -> bool:
        if isinstance(request, UploadArtifactRequest):
            scope = RuntimeScope(
                principal_id=principal,
                session_handle=request.session_handle,
            )
            result = await self._artifacts.upload(
                scope,
                ArtifactUploadRequest(
                    data_url=request.data_url,
                    media_type=request.media_type,
                    name=request.name,
                    caption=request.caption,
                ),
            )
            await send(ArtifactUploadedMessage(
                request_id=request.request_id,
                result=result,
            ))
        elif isinstance(request, DownloadArtifactRequest):
            scope = RuntimeScope(
                principal_id=principal,
                session_handle=request.session_handle,
            )
            result = await self._artifacts.download(
                scope,
                ArtifactDownloadRequest(artifact_id=request.artifact_id),
            )
            delivered = result.model_copy(update={
                "artifact": result.artifact.model_copy(update={"status": "delivered"})
            })
            await send(ArtifactDownloadedMessage(
                request_id=request.request_id,
                result=delivered,
            ))
            try:
                await self._artifacts.acknowledge_delivery(scope, request.artifact_id)
            except Exception:
                pass
        elif isinstance(request, TranscribeArtifactRequest):
            if self._transcription is None:
                raise TranscriptionUnavailableError("Audio transcription is unavailable")
            scope = RuntimeScope(
                principal_id=principal,
                session_handle=request.session_handle,
            )
            result = await self._transcription.transcribe(
                scope,
                ArtifactTranscriptionRequest(artifact_id=request.artifact_id),
            )
            await send(ArtifactTranscribedMessage(
                request_id=request.request_id,
                result=result,
            ))
        else:
            return False
        return True
