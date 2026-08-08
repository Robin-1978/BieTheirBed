from __future__ import annotations

import base64
import stat

import pytest

from pc_assistant.agent_runtime.contracts import ArtifactDownloadResult
from pc_assistant.artifacts import ArtifactRef
from pc_assistant.artifacts.delivery import save_download


def _result(data: bytes, *, name: str = "capture.png") -> ArtifactDownloadResult:
    return ArtifactDownloadResult(
        artifact=ArtifactRef(
            artifact_id="artifact-a",
            kind="image",
            name=name,
            media_type="image/png",
            size=len(data),
            status="delivered",
        ),
        data_url=f"data:image/png;base64,{base64.b64encode(data).decode('ascii')}",
    )


def test_save_download_writes_bounded_client_file(tmp_path) -> None:
    target = save_download(_result(b"png-data", name="../capture.png"), tmp_path)

    assert target.parent == tmp_path
    assert target.name == "artifact-a-capture.png"
    assert target.read_bytes() == b"png-data"
    assert stat.S_IMODE(target.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(target.stat().st_mode) == 0o600


def test_save_download_rejects_metadata_mismatch(tmp_path) -> None:
    result = _result(b"png-data").model_copy(
        update={"data_url": "data:image/jpeg;base64,cG5nLWRhdGE="}
    )

    with pytest.raises(ValueError, match="media type"):
        save_download(result, tmp_path)
