"""Client-side persistence for artifacts downloaded through Core."""
from __future__ import annotations

import base64
from pathlib import Path
from uuid import uuid4

from pc_assistant.agent_runtime.contracts import ArtifactDownloadResult


def save_download(result: ArtifactDownloadResult, directory: str | Path) -> Path:
    """Decode one validated download into a deterministic local file."""
    prefix = f"data:{result.artifact.media_type};base64,"
    if not result.data_url.startswith(prefix):
        raise ValueError("Artifact media type does not match its data URL")
    try:
        data = base64.b64decode(result.data_url[len(prefix) :], validate=True)
    except Exception as exc:
        raise ValueError("Artifact download contains invalid base64") from exc
    if len(data) != result.artifact.size:
        raise ValueError("Artifact download size does not match its metadata")

    target_dir = Path(directory).expanduser().resolve()
    target_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    target_dir.chmod(0o700)
    safe_name = _safe_name(result.artifact.name)
    target = target_dir / f"{result.artifact.artifact_id}-{safe_name}"
    temporary = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
    temporary.write_bytes(data)
    temporary.chmod(0o600)
    temporary.replace(target)
    return target


def _safe_name(name: str) -> str:
    cleaned = "".join(
        character
        for character in Path(name).name
        if character.isalnum() or character in "._- "
    ).strip()
    return cleaned[:160] or "artifact.bin"
