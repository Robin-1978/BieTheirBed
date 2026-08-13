"""Artifact operations mixed into the transport-only Core client."""
from __future__ import annotations

from knoa_platform.agent_runtime.contracts import (
    ArtifactDownloadResult,
    ArtifactTranscriptionResult,
)
from knoa_platform.artifacts import ArtifactRef
from knoa_platform.service.core_api import (
    ArtifactDownloadedMessage,
    ArtifactTranscribedMessage,
    ArtifactUploadedMessage,
    DownloadArtifactRequest,
    TranscribeArtifactRequest,
    UploadArtifactRequest,
)


class CoreArtifactClientMixin:
    async def upload_artifact(
        self,
        session_handle: str,
        data_url: str,
        *,
        media_type: str = "image/jpeg",
        name: str = "",
        caption: str = "",
    ) -> ArtifactRef:
        response = await self._request(
            UploadArtifactRequest(
                request_id=self._request_id(),
                session_handle=session_handle,
                data_url=data_url,
                media_type=media_type,
                name=name,
                caption=caption,
            )
        )
        if not isinstance(response, ArtifactUploadedMessage):
            raise RuntimeError("CoreServer returned an invalid artifact response")
        return response.result

    async def download_artifact(
        self,
        session_handle: str,
        artifact_id: str,
    ) -> ArtifactDownloadResult:
        response = await self._request(
            DownloadArtifactRequest(
                request_id=self._request_id(),
                session_handle=session_handle,
                artifact_id=artifact_id,
            )
        )
        if not isinstance(response, ArtifactDownloadedMessage):
            raise RuntimeError(
                "CoreServer returned an invalid artifact download response"
            )
        return response.result

    async def transcribe_artifact(
        self,
        session_handle: str,
        artifact_id: str,
    ) -> ArtifactTranscriptionResult:
        response = await self._request(
            TranscribeArtifactRequest(
                request_id=self._request_id(),
                session_handle=session_handle,
                artifact_id=artifact_id,
            )
        )
        if not isinstance(response, ArtifactTranscribedMessage):
            raise RuntimeError(
                "CoreServer returned an invalid artifact transcription response"
            )
        return response.result
