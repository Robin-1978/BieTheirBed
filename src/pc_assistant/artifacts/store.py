"""Session-scoped registry for user-deliverable files."""
from __future__ import annotations

import hashlib
import mimetypes
import shutil
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class _Artifact:
    artifact_id: str
    session_key: str
    path: Path
    name: str
    media_type: str
    kind: str
    size: int
    expires_at: float


class ArtifactStore:
    """Own temporary deliverable artifacts without exposing server paths."""

    def __init__(
        self,
        root: str | Path,
        *,
        ttl_seconds: float = 3600,
        max_bytes: int = 100 * 1024 * 1024,
        clock=time.time,
    ) -> None:
        self.root = Path(root).expanduser().resolve()
        self._ttl = max(1.0, ttl_seconds)
        self._max_bytes = max(1, max_bytes)
        self._clock = clock
        self._entries: dict[str, _Artifact] = {}

    @staticmethod
    def _session_key(session_id: str) -> str:
        return hashlib.sha256((session_id or "default").encode()).hexdigest()[:20]

    @staticmethod
    def _kind(media_type: str) -> str:
        return "image" if media_type.startswith("image/") else "file"

    @staticmethod
    def _safe_name(name: str) -> str:
        cleaned = "".join(ch for ch in Path(name).name if ch.isalnum() or ch in "._- ").strip()
        return cleaned[:160] or "artifact.bin"

    def _entry(
        self,
        session_id: str,
        artifact_id: str,
        path: Path,
        *,
        name: str,
        media_type: str,
    ) -> dict[str, Any]:
        size = path.stat().st_size
        if size <= 0 or size > self._max_bytes:
            raise ValueError(f"Artifact size must be between 1 and {self._max_bytes} bytes")
        normalized_media_type = media_type or "application/octet-stream"
        entry = _Artifact(
            artifact_id=artifact_id,
            session_key=self._session_key(session_id),
            path=path,
            name=self._safe_name(name),
            media_type=normalized_media_type,
            kind=self._kind(normalized_media_type),
            size=size,
            expires_at=self._clock() + self._ttl,
        )
        self._entries[artifact_id] = entry
        return self.public_ref(session_id, artifact_id)

    def register_managed(
        self,
        session_id: str,
        path: str | Path,
        *,
        media_type: str = "",
        name: str = "",
    ) -> dict[str, Any]:
        resolved = Path(path).expanduser().resolve()
        try:
            resolved.relative_to(self.root.parent)
        except ValueError as exc:
            raise ValueError("Managed artifact must stay below the runtime attachment root") from exc
        if not resolved.is_file():
            raise ValueError(f"Artifact file does not exist: {resolved}")
        detected = media_type or mimetypes.guess_type(resolved.name)[0] or "application/octet-stream"
        return self._entry(
            session_id,
            uuid.uuid4().hex,
            resolved,
            name=name or resolved.name,
            media_type=detected,
        )

    def prepare_path(self, session_id: str, path: str | Path) -> dict[str, Any]:
        source = Path(path).expanduser().resolve()
        if not source.is_file():
            raise ValueError(f"File does not exist: {source}")
        size = source.stat().st_size
        if size <= 0 or size > self._max_bytes:
            raise ValueError(f"Artifact size must be between 1 and {self._max_bytes} bytes")
        artifact_id = uuid.uuid4().hex
        directory = self.root / self._session_key(session_id)
        directory.mkdir(parents=True, exist_ok=True)
        safe_name = self._safe_name(source.name)
        destination = directory / f"{artifact_id}-{safe_name}"
        temporary = directory / f".{artifact_id}.tmp"
        shutil.copyfile(source, temporary)
        temporary.replace(destination)
        media_type = mimetypes.guess_type(safe_name)[0] or "application/octet-stream"
        return self._entry(
            session_id,
            artifact_id,
            destination,
            name=safe_name,
            media_type=media_type,
        )

    def public_ref(self, session_id: str, artifact_id: str) -> dict[str, Any]:
        entry = self._get(session_id, artifact_id)
        return {
            "artifact_id": entry.artifact_id,
            "kind": entry.kind,
            "name": entry.name,
            "media_type": entry.media_type,
            "size": entry.size,
            "visibility": "user",
            "temporary": True,
        }

    def resolve(self, session_id: str, artifact_id: str) -> dict[str, Any]:
        entry = self._get(session_id, artifact_id)
        return {**self.public_ref(session_id, artifact_id), "path": str(entry.path)}

    def _get(self, session_id: str, artifact_id: str) -> _Artifact:
        entry = self._entries.get(artifact_id)
        if entry is None or entry.session_key != self._session_key(session_id):
            raise KeyError(f"Artifact not found: {artifact_id}")
        if entry.expires_at <= self._clock() or not entry.path.is_file():
            self._entries.pop(artifact_id, None)
            raise KeyError(f"Artifact expired: {artifact_id}")
        return entry

    def cleanup_session(self, session_id: str) -> None:
        key = self._session_key(session_id)
        for artifact_id, entry in list(self._entries.items()):
            if entry.session_key == key:
                self._entries.pop(artifact_id, None)
                try:
                    entry.path.unlink(missing_ok=True)
                except OSError:
                    pass
        session_dir = self.root / key
        try:
            session_dir.rmdir()
        except OSError:
            pass

    def cleanup_expired(self) -> None:
        now = self._clock()
        for artifact_id, entry in list(self._entries.items()):
            if entry.expires_at <= now or not entry.path.is_file():
                self._entries.pop(artifact_id, None)
                try:
                    entry.path.unlink(missing_ok=True)
                except OSError:
                    pass
                try:
                    entry.path.parent.rmdir()
                except OSError:
                    pass
