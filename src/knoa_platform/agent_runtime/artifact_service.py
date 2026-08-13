"""Principal- and session-scoped artifact ingress."""
from __future__ import annotations

import asyncio

from knoa_platform.agent_runtime.contracts import (
    ArtifactDownloadRequest,
    ArtifactDownloadResult,
    ArtifactUploadRequest,
    RuntimeScope,
)
from knoa_platform.agent_runtime.session_store import RuntimeSessionRepository
from knoa_platform.artifacts import ArtifactRef, ArtifactStore


class InvalidArtifactError(ValueError):
    """Raised when an uploaded artifact fails bounded ingress validation."""


class ArtifactNotFoundError(LookupError):
    """Raised when an artifact is absent from the owned session."""


class ArtifactDownloadTooLargeError(ValueError):
    """Raised when an artifact cannot fit inside one Core response."""


MAX_ARTIFACT_DOWNLOAD_BYTES = ((64 * 1024 * 1024) - 256) * 3 // 4


class ArtifactService:
    """Validate ownership before admitting bytes into the artifact store."""

    def __init__(
        self,
        sessions: RuntimeSessionRepository,
        store: ArtifactStore,
    ) -> None:
        self._sessions = sessions
        self._store = store

    async def upload(
        self,
        scope: RuntimeScope,
        request: ArtifactUploadRequest,
    ) -> ArtifactRef:
        owned = await asyncio.to_thread(
            self._sessions.resolve,
            scope.principal_id,
            scope.session_handle,
        )
        try:
            raw_ref = await asyncio.to_thread(
                self._store.put_data_url,
                owned.session_handle,
                request.data_url,
                media_type=request.media_type,
                name=request.name,
                source="core-api-upload",
                caption=request.caption,
            )
        except ValueError as exc:
            raise InvalidArtifactError(str(exc)) from exc
        bounded_ref = {
            field_name: raw_ref[field_name]
            for field_name in ArtifactRef.model_fields
            if field_name in raw_ref
        }
        return ArtifactRef.model_validate(bounded_ref)

    async def download(
        self,
        scope: RuntimeScope,
        request: ArtifactDownloadRequest,
    ) -> ArtifactDownloadResult:
        owned = await asyncio.to_thread(
            self._sessions.resolve,
            scope.principal_id,
            scope.session_handle,
        )
        try:
            data_url = await asyncio.to_thread(
                self._store.read_data_url,
                owned.session_handle,
                request.artifact_id,
                max_bytes=MAX_ARTIFACT_DOWNLOAD_BYTES,
            )
            raw_ref = await asyncio.to_thread(
                self._store.public_ref,
                owned.session_handle,
                request.artifact_id,
            )
        except (KeyError, OSError) as exc:
            raise ArtifactNotFoundError(request.artifact_id) from exc
        except ValueError as exc:
            raise ArtifactDownloadTooLargeError(str(exc)) from exc
        bounded_ref = {
            field_name: raw_ref[field_name]
            for field_name in ArtifactRef.model_fields
            if field_name in raw_ref
        }
        artifact = ArtifactRef.model_validate(bounded_ref)
        return ArtifactDownloadResult(artifact=artifact, data_url=data_url)

    async def acknowledge_delivery(
        self,
        scope: RuntimeScope,
        artifact_id: str,
    ) -> None:
        owned = await asyncio.to_thread(
            self._sessions.resolve,
            scope.principal_id,
            scope.session_handle,
        )
        try:
            await asyncio.to_thread(
                self._store.mark_delivered,
                owned.session_handle,
                artifact_id,
            )
        except KeyError as exc:
            raise ArtifactNotFoundError(artifact_id) from exc
