from __future__ import annotations

from pathlib import Path

import pytest

from knoa_platform.agent_runtime.artifact_service import (
    ArtifactDownloadTooLargeError,
    ArtifactNotFoundError,
    ArtifactService,
    InvalidArtifactError,
)
from knoa_platform.agent_runtime.contracts import (
    ArtifactDownloadRequest,
    ArtifactUploadRequest,
    RuntimeScope,
)
from knoa_platform.agent_runtime.session_store import RuntimeSessionRepository
from knoa_platform.artifacts import ArtifactStore
from knoa_platform.exceptions import SessionNotFoundError


DATA_URL = (
    "data:image/png;base64,"
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGP4z8AAAAMBAQDJ/pLvAAAAAElFTkSuQmCC"
)
TEXT_DATA_URL = "data:text/plain;base64,SGVsbG8sIEtub2Eh"


def _service(tmp_path: Path) -> tuple[ArtifactService, RuntimeSessionRepository]:
    sessions = RuntimeSessionRepository(
        tmp_path / "assistant.db",
        handle_factory=lambda: "opaque-session",
    )
    store = ArtifactStore(
        tmp_path / "attachments",
        db_path=tmp_path / "assistant.db",
    )
    return ArtifactService(sessions, store), sessions


@pytest.mark.asyncio
async def test_upload_is_scoped_and_returns_bounded_reference(tmp_path: Path) -> None:
    service, sessions = _service(tmp_path)
    scope = sessions.create("principal-a")

    result = await service.upload(
        scope,
        ArtifactUploadRequest(data_url=DATA_URL, caption="sample"),
    )

    assert result.artifact_id
    assert result.media_type == "image/png"
    assert result.visibility == "agent"
    assert "base64" not in result.model_dump_json()


@pytest.mark.asyncio
async def test_upload_preserves_owned_file_name_and_type(tmp_path: Path) -> None:
    service, sessions = _service(tmp_path)
    scope = sessions.create("principal-a")

    result = await service.upload(
        scope,
        ArtifactUploadRequest(
            data_url=TEXT_DATA_URL,
            media_type="text/plain",
            name="notes.txt",
            caption="meeting notes",
        ),
    )

    assert result.kind == "file"
    assert result.name == "notes.txt"
    assert result.media_type == "text/plain"


@pytest.mark.asyncio
async def test_upload_rejects_foreign_scope_before_storing(tmp_path: Path) -> None:
    service, sessions = _service(tmp_path)
    owned = sessions.create("principal-a")

    with pytest.raises(SessionNotFoundError):
        await service.upload(
            RuntimeScope(
                principal_id="principal-b",
                session_handle=owned.session_handle,
            ),
            ArtifactUploadRequest(data_url=DATA_URL),
        )

    assert not list((tmp_path / "attachments").rglob("*.png"))


@pytest.mark.asyncio
async def test_upload_maps_invalid_payload_to_public_validation_error(tmp_path: Path) -> None:
    service, sessions = _service(tmp_path)
    scope = sessions.create("principal-a")

    with pytest.raises(InvalidArtifactError):
        await service.upload(
            scope,
            ArtifactUploadRequest(data_url="data:image/png;base64,AAAA"),
        )


@pytest.mark.asyncio
async def test_download_returns_bytes_without_marking_delivery(tmp_path: Path) -> None:
    service, sessions = _service(tmp_path)
    scope = sessions.create("principal-a")
    uploaded = await service.upload(scope, ArtifactUploadRequest(data_url=DATA_URL))

    result = await service.download(
        scope,
        ArtifactDownloadRequest(artifact_id=uploaded.artifact_id),
    )

    assert result.data_url == DATA_URL
    assert result.artifact.status == "available"
    assert "path" not in result.model_dump_json()


@pytest.mark.asyncio
async def test_delivery_acknowledgement_marks_artifact_after_transport_send(
    tmp_path: Path,
) -> None:
    service, sessions = _service(tmp_path)
    scope = sessions.create("principal-a")
    uploaded = await service.upload(scope, ArtifactUploadRequest(data_url=DATA_URL))

    await service.acknowledge_delivery(scope, uploaded.artifact_id)
    result = await service.download(
        scope,
        ArtifactDownloadRequest(artifact_id=uploaded.artifact_id),
    )

    assert result.artifact.status == "delivered"


@pytest.mark.asyncio
async def test_download_hides_missing_artifact(tmp_path: Path) -> None:
    service, sessions = _service(tmp_path)
    scope = sessions.create("principal-a")

    with pytest.raises(ArtifactNotFoundError):
        await service.download(
            scope,
            ArtifactDownloadRequest(artifact_id="missing"),
        )


@pytest.mark.asyncio
async def test_download_rejects_artifact_above_wire_limit(
    tmp_path: Path,
    monkeypatch,
) -> None:
    service, sessions = _service(tmp_path)
    scope = sessions.create("principal-a")
    uploaded = await service.upload(scope, ArtifactUploadRequest(data_url=DATA_URL))
    monkeypatch.setattr(
        "knoa_platform.agent_runtime.artifact_service.MAX_ARTIFACT_DOWNLOAD_BYTES",
        1,
    )

    with pytest.raises(ArtifactDownloadTooLargeError):
        await service.download(
            scope,
            ArtifactDownloadRequest(artifact_id=uploaded.artifact_id),
        )
