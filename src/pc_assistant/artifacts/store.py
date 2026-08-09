"""Unified, session-scoped storage for inbound and outbound artifacts.

History and public events contain only opaque IDs and bounded metadata. Paths
stay inside this store; bytes cross the Core boundary only through explicit,
session-scoped delivery APIs.
"""
from __future__ import annotations

import base64
import copy
import hashlib
import mimetypes
import sqlite3
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pc_assistant.artifacts.models import ArtifactRef
from pc_assistant.sqlite_schema import require_exact_table

Direction = Literal["inbound", "outbound"]
Ownership = Literal["borrowed", "managed", "generated"]
Retention = Literal["temporary", "session", "persistent"]
_TEXT_FILE_SUFFIXES = frozenset(
    {
        ".c",
        ".cc",
        ".conf",
        ".cpp",
        ".csv",
        ".go",
        ".h",
        ".hpp",
        ".html",
        ".ini",
        ".java",
        ".js",
        ".json",
        ".log",
        ".md",
        ".py",
        ".rs",
        ".sh",
        ".sql",
        ".toml",
        ".ts",
        ".txt",
        ".xml",
        ".yaml",
        ".yml",
    }
)


@dataclass
class _Artifact:
    artifact_id: str
    session_key: str
    path: Path
    name: str
    media_type: str
    kind: str
    size: int
    direction: Direction
    ownership: Ownership
    retention: Retention
    expires_at: float | None
    width: int = 0
    height: int = 0
    content_sha256: str = ""
    delivered_at: float | None = None


class ArtifactStore:
    """One ownership-aware store for model inputs and user deliverables."""

    def __init__(
        self,
        root: str | Path = "attachments",
        *,
        persistent_root: str | Path | None = None,
        db_path: str | Path | None = None,
        ttl_seconds: float = 3600,
        delivery_grace_seconds: float = 300,
        max_bytes: int = 100 * 1024 * 1024,
        clock=time.time,
    ) -> None:
        self.root = Path(root).expanduser().resolve()
        self.persistent_root = (
            Path(persistent_root).expanduser().resolve()
            if persistent_root is not None
            else self.root.parent / "artifacts"
        )
        self._db_path = (
            Path(db_path).expanduser().resolve()
            if db_path is not None
            else self.persistent_root.parent / "data" / "assistant.db"
        )
        self._ttl = max(1.0, ttl_seconds)
        self._delivery_grace = max(1.0, delivery_grace_seconds)
        self._max_bytes = max(1, max_bytes)
        self._clock = clock
        self._entries: dict[str, _Artifact] = {}
        self._init_registry()
        self._load_registry()
        self.cleanup_expired()

    def _connect(self) -> sqlite3.Connection:
        self._db_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self._db_path.parent.chmod(0o700)
        connection = sqlite3.connect(self._db_path)
        self._db_path.chmod(0o600)
        return connection

    def _init_registry(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS artifact_registry (
                    artifact_id TEXT PRIMARY KEY,
                    session_key TEXT NOT NULL,
                    path TEXT NOT NULL,
                    name TEXT NOT NULL,
                    media_type TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    size INTEGER NOT NULL,
                    direction TEXT NOT NULL,
                    ownership TEXT NOT NULL,
                    retention TEXT NOT NULL,
                    width INTEGER NOT NULL DEFAULT 0,
                    height INTEGER NOT NULL DEFAULT 0,
                    content_sha256 TEXT NOT NULL DEFAULT '',
                    expires_at REAL,
                    delivered_at REAL
                )
                """
            )
            require_exact_table(
                connection,
                "artifact_registry",
                (
                    ("artifact_id", "TEXT", False, None, 1),
                    ("session_key", "TEXT", True, None, 0),
                    ("path", "TEXT", True, None, 0),
                    ("name", "TEXT", True, None, 0),
                    ("media_type", "TEXT", True, None, 0),
                    ("kind", "TEXT", True, None, 0),
                    ("size", "INTEGER", True, None, 0),
                    ("direction", "TEXT", True, None, 0),
                    ("ownership", "TEXT", True, None, 0),
                    ("retention", "TEXT", True, None, 0),
                    ("width", "INTEGER", True, "0", 0),
                    ("height", "INTEGER", True, "0", 0),
                    ("content_sha256", "TEXT", True, "''", 0),
                    ("expires_at", "REAL", False, None, 0),
                    ("delivered_at", "REAL", False, None, 0),
                ),
                label="Artifact registry",
            )

    def _load_registry(self) -> None:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT artifact_id, session_key, path, name, media_type, kind,
                       size, direction, ownership, retention, width, height,
                       content_sha256, expires_at, delivered_at
                FROM artifact_registry
                """
            ).fetchall()
        for row in rows:
            path = Path(row[2]).expanduser().resolve()
            ownership = row[8]
            retention = row[9]
            allowed_root = self.persistent_root if retention == "persistent" else self.root
            try:
                if ownership != "borrowed":
                    path.relative_to(allowed_root)
            except ValueError:
                self._delete_registry(row[0])
                continue
            if not path.is_file():
                self._delete_registry(row[0])
                continue
            self._entries[row[0]] = _Artifact(
                artifact_id=row[0],
                session_key=row[1],
                path=path,
                name=row[3],
                media_type=row[4],
                kind=row[5],
                size=int(row[6]),
                direction=row[7],
                ownership=ownership,
                retention=retention,
                expires_at=row[13],
                width=int(row[10]),
                height=int(row[11]),
                content_sha256=row[12],
                delivered_at=row[14],
            )

    def _persist(self, entry: _Artifact) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO artifact_registry (
                    artifact_id, session_key, path, name, media_type, kind,
                    size, direction, ownership, retention, width, height,
                    content_sha256, expires_at, delivered_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    entry.artifact_id,
                    entry.session_key,
                    str(entry.path),
                    entry.name,
                    entry.media_type,
                    entry.kind,
                    entry.size,
                    entry.direction,
                    entry.ownership,
                    entry.retention,
                    entry.width,
                    entry.height,
                    entry.content_sha256,
                    entry.expires_at,
                    entry.delivered_at,
                ),
            )

    def _delete_registry(self, artifact_id: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM artifact_registry WHERE artifact_id = ?",
                (artifact_id,),
            )

    @staticmethod
    def _session_key(session_id: str) -> str:
        normalized = session_id.strip()
        if not normalized or len(normalized) > 256:
            raise ValueError("Artifact session ID must contain 1-256 characters")
        return hashlib.sha256(normalized.encode()).hexdigest()

    @staticmethod
    def _kind(media_type: str) -> str:
        return "image" if media_type.startswith("image/") else "file"

    @staticmethod
    def _safe_name(name: str) -> str:
        cleaned = "".join(
            ch for ch in Path(name).name if ch.isalnum() or ch in "._- "
        ).strip()
        if cleaned in {".", ".."}:
            cleaned = ""
        return cleaned[:160] or "artifact.bin"

    @staticmethod
    def _decode_data_url(data_url: str, fallback_media_type: str) -> tuple[bytes, str]:
        if not data_url.startswith("data:") or "," not in data_url:
            raise ValueError("Artifact must be a data URL")
        metadata, encoded = data_url.split(",", 1)
        metadata_parts = metadata[5:].split(";")
        if "base64" not in metadata_parts[1:]:
            raise ValueError("Artifact data URL must use base64 encoding")
        media_type = metadata_parts[0] or fallback_media_type
        if (
            len(media_type) > 128
            or "/" not in media_type
            or any(character.isspace() for character in media_type)
        ):
            raise ValueError("Artifact media type is invalid")
        try:
            data = base64.b64decode(encoded, validate=True)
        except Exception as exc:
            raise ValueError("Artifact contains invalid base64") from exc
        return data, media_type

    @staticmethod
    def _detect_image(data: bytes, fallback_media_type: str) -> tuple[str, str, int, int]:
        formats = {
            "JPEG": ("image/jpeg", ".jpg"),
            "PNG": ("image/png", ".png"),
            "WEBP": ("image/webp", ".webp"),
            "GIF": ("image/gif", ".gif"),
        }
        try:
            import io
            from PIL import Image

            with Image.open(io.BytesIO(data)) as image:
                media_type, suffix = formats.get(
                    str(image.format or "").upper(),
                    (fallback_media_type, ".img"),
                )
                return media_type, suffix, int(image.width), int(image.height)
        except Exception:
            suffix = {
                "image/jpeg": ".jpg",
                "image/png": ".png",
                "image/webp": ".webp",
                "image/gif": ".gif",
            }.get(fallback_media_type, ".img")
            return fallback_media_type, suffix, 0, 0

    def _expires_at(self, retention: Retention) -> float | None:
        return None if retention == "persistent" else self._clock() + self._ttl

    def _register(
        self,
        session_id: str,
        path: Path,
        *,
        direction: Direction,
        ownership: Ownership,
        retention: Retention,
        name: str = "",
        media_type: str = "",
        width: int = 0,
        height: int = 0,
        content_sha256: str = "",
    ) -> _Artifact:
        self.cleanup_expired()
        if not path.is_file():
            raise ValueError(f"Artifact file does not exist: {path}")
        if ownership != "borrowed":
            path.parent.chmod(0o700)
            path.chmod(0o600)
        size = path.stat().st_size
        if size <= 0 or size > self._max_bytes:
            raise ValueError(f"Artifact size must be between 1 and {self._max_bytes} bytes")
        normalized_media_type = (
            media_type or mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        )
        artifact_id = uuid.uuid4().hex
        entry = _Artifact(
            artifact_id=artifact_id,
            session_key=self._session_key(session_id),
            path=path,
            name=self._safe_name(name or path.name),
            media_type=normalized_media_type,
            kind=self._kind(normalized_media_type),
            size=size,
            direction=direction,
            ownership=ownership,
            retention=retention,
            expires_at=self._expires_at(retention),
            width=width,
            height=height,
            content_sha256=content_sha256,
        )
        self._entries[artifact_id] = entry
        self._persist(entry)
        return entry

    # ------------------------------------------------------------------
    # Inbound artifacts
    # ------------------------------------------------------------------

    def put_data_url(
        self,
        session_id: str,
        data_url: str,
        *,
        media_type: str = "image/jpeg",
        name: str = "",
        source: str = "upload",
        caption: str = "",
    ) -> dict[str, Any]:
        data, detected_media = self._decode_data_url(data_url, media_type)
        if not data or len(data) > self._max_bytes:
            raise ValueError(f"Artifact size must be between 1 and {self._max_bytes} bytes")
        safe_name = self._safe_name(name) if name else ""
        width = height = 0
        if detected_media.startswith("image/"):
            detected_media, suffix, width, height = self._detect_image(
                data,
                detected_media,
            )
            if width <= 0 or height <= 0:
                raise ValueError("Artifact is not a supported image")
        else:
            suffix = Path(safe_name).suffix
            if not suffix:
                suffix = mimetypes.guess_extension(detected_media) or ".bin"
        directory = self.root / self._session_key(session_id) / "inbound"
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        directory.chmod(0o700)
        artifact_id = uuid.uuid4().hex
        path = directory / f"{artifact_id}{suffix}"
        temporary = directory / f".{artifact_id}.tmp"
        temporary.write_bytes(data)
        temporary.chmod(0o600)
        temporary.replace(path)
        entry = self._register(
            session_id,
            path,
            direction="inbound",
            ownership="managed",
            retention="session",
            name=safe_name,
            media_type=detected_media,
            width=width,
            height=height,
            content_sha256=hashlib.sha256(data).hexdigest(),
        )
        return self._inbound_ref(entry, source=source, caption=caption)

    def register_path(
        self,
        session_id: str,
        path: str | Path,
        *,
        media_type: str = "image/png",
        source: str,
        caption: str = "",
    ) -> dict[str, Any]:
        """Register a Core-owned image path without copying it."""
        resolved = Path(path).expanduser().resolve()
        try:
            resolved.relative_to(self.root)
        except ValueError as exc:
            raise ValueError("Managed artifact must stay below the artifact root") from exc
        with resolved.open("rb") as stream:
            data = stream.read(self._max_bytes + 1)
        if not data or len(data) > self._max_bytes:
            raise ValueError(f"Artifact size must be between 1 and {self._max_bytes} bytes")
        detected_media, _suffix, width, height = self._detect_image(data, media_type)
        if width <= 0 or height <= 0:
            raise ValueError("Artifact is not a supported image")
        entry = self._register(
            session_id,
            resolved,
            direction="inbound",
            ownership="generated",
            retention="session",
            media_type=detected_media,
            width=width,
            height=height,
            content_sha256=hashlib.sha256(data).hexdigest(),
        )
        return self._inbound_ref(entry, source=source, caption=caption)

    @staticmethod
    def _inbound_ref(
        entry: _Artifact,
        *,
        source: str,
        caption: str = "",
    ) -> dict[str, Any]:
        ref: dict[str, Any] = {
            "type": "image_ref" if entry.kind == "image" else "file_ref",
            "artifact_id": entry.artifact_id,
            "kind": entry.kind,
            "name": entry.name,
            "media_type": entry.media_type,
            "size": entry.size,
            "direction": entry.direction,
            "ownership": entry.ownership,
            "retention": entry.retention,
            "status": "available",
            "visibility": "agent",
            "width": entry.width,
            "height": entry.height,
            "source": source,
        }
        if caption:
            ref["caption"] = caption[:200]
        return ref

    # ------------------------------------------------------------------
    # Outbound artifacts
    # ------------------------------------------------------------------

    def prepare_path(self, session_id: str, path: str | Path) -> dict[str, Any]:
        """Borrow an existing user file for delivery; never copy or delete it."""
        source = Path(path).expanduser().resolve()
        entry = self._register(
            session_id,
            source,
            direction="outbound",
            ownership="borrowed",
            retention="temporary",
        )
        return self.public_ref(session_id, entry.artifact_id)

    def register_generated(
        self,
        session_id: str,
        path: str | Path,
        *,
        media_type: str = "",
        name: str = "",
        retention: Retention = "temporary",
    ) -> dict[str, Any]:
        resolved = Path(path).expanduser().resolve()
        allowed_root = self.persistent_root if retention == "persistent" else self.root
        try:
            resolved.relative_to(allowed_root)
        except ValueError as exc:
            raise ValueError(
                f"Generated {retention} artifact must stay below {allowed_root}"
            ) from exc
        entry = self._register(
            session_id,
            resolved,
            direction="outbound",
            ownership="generated",
            retention=retention,
            media_type=media_type,
            name=name,
        )
        return self.public_ref(session_id, entry.artifact_id)

    def create_generated_text(
        self,
        session_id: str,
        content: str,
        *,
        name: str = "result.md",
        retention: Retention = "persistent",
    ) -> dict[str, Any]:
        """Create one Core-owned UTF-8 result artifact atomically."""
        data = content.encode("utf-8")
        if not data or len(data) > self._max_bytes:
            raise ValueError(
                f"Artifact size must be between 1 and {self._max_bytes} bytes"
            )
        root = self.persistent_root if retention == "persistent" else self.root
        directory = root / self._session_key(session_id) / "generated"
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        directory.chmod(0o700)
        artifact_key = uuid.uuid4().hex
        safe_name = self._safe_name(name)
        suffix = Path(safe_name).suffix or ".md"
        path = directory / f"{artifact_key}{suffix}"
        temporary = directory / f".{artifact_key}.tmp"
        temporary.write_bytes(data)
        temporary.chmod(0o600)
        temporary.replace(path)
        entry = self._register(
            session_id,
            path,
            direction="outbound",
            ownership="generated",
            retention=retention,
            name=safe_name,
            media_type="text/markdown",
            content_sha256=hashlib.sha256(data).hexdigest(),
        )
        return self.public_ref(session_id, entry.artifact_id)

    def public_ref(self, session_id: str, artifact_id: str) -> dict[str, Any]:
        entry = self._get(session_id, artifact_id)
        return ArtifactRef(
            artifact_id=entry.artifact_id,
            kind=entry.kind,
            name=entry.name,
            media_type=entry.media_type,
            size=entry.size,
            direction=entry.direction,
            ownership=entry.ownership,
            retention=entry.retention,
            status="delivered" if entry.delivered_at else "available",
            visibility="user",
        ).model_dump()

    def read_data_url(
        self,
        session_id: str,
        artifact_id: str,
        *,
        max_bytes: int,
    ) -> str:
        """Read bounded artifact bytes without exposing the backing path."""
        entry = self._get(session_id, artifact_id)
        current_size = entry.path.stat().st_size
        if current_size != entry.size:
            raise OSError(f"Artifact size changed before delivery: {artifact_id}")
        if current_size > max_bytes:
            raise ValueError(f"Artifact exceeds the {max_bytes} byte download limit")
        with entry.path.open("rb") as stream:
            data = stream.read(max_bytes + 1)
        if len(data) != current_size:
            raise OSError(f"Artifact size changed while reading: {artifact_id}")
        encoded = base64.b64encode(data).decode("ascii")
        return f"data:{entry.media_type};base64,{encoded}"

    def read_text(
        self,
        session_id: str,
        artifact_id: str,
        *,
        max_bytes: int = 512_000,
    ) -> dict[str, Any]:
        """Read bounded text from an owned inbound file without exposing its path."""
        entry = self._get(session_id, artifact_id)
        if entry.kind != "file":
            raise ValueError("Artifact is not a file")
        textual = (
            entry.media_type.startswith("text/")
            or entry.media_type
            in {
                "application/json",
                "application/javascript",
                "application/sql",
                "application/toml",
                "application/xml",
                "application/x-yaml",
            }
            or Path(entry.name).suffix.lower() in _TEXT_FILE_SUFFIXES
        )
        if not textual:
            raise ValueError(
                f"Artifact type is not readable as text: {entry.media_type}"
            )
        bounded = max(1, min(max_bytes, 512_000))
        with entry.path.open("rb") as stream:
            data = stream.read(bounded + 1)
        truncated = len(data) > bounded
        data = data[:bounded]
        if b"\x00" in data:
            raise ValueError("Artifact contains binary data")
        return {
            "artifact_id": entry.artifact_id,
            "name": entry.name,
            "media_type": entry.media_type,
            "content": data.decode("utf-8", errors="replace"),
            "size": entry.size,
            "truncated": truncated,
        }

    def mark_delivered(self, session_id: str, artifact_id: str) -> dict[str, Any]:
        """Acknowledge client delivery; Core retains cleanup authority."""
        entry = self._get(session_id, artifact_id)
        entry.delivered_at = self._clock()
        if entry.retention == "temporary":
            grace_expiry = entry.delivered_at + self._delivery_grace
            entry.expires_at = min(entry.expires_at or grace_expiry, grace_expiry)
        self._persist(entry)
        return self.public_ref(session_id, artifact_id)

    # ------------------------------------------------------------------
    # Request hydration / manifests
    # ------------------------------------------------------------------

    def _get(self, session_id: str, artifact_id: str) -> _Artifact:
        entry = self._entries.get(artifact_id)
        if entry is None or entry.session_key != self._session_key(session_id):
            raise KeyError(f"Artifact not found: {artifact_id}")
        if entry.expires_at is not None and entry.expires_at <= self._clock():
            self._discard(entry)
            raise KeyError(f"Artifact expired: {artifact_id}")
        if not entry.path.is_file():
            self._entries.pop(artifact_id, None)
            self._delete_registry(artifact_id)
            raise KeyError(f"Artifact file is unavailable: {artifact_id}")
        return entry

    @staticmethod
    def _ref_id(ref: dict[str, Any]) -> str:
        return str(ref.get("artifact_id") or "")

    def hydrate_ref(self, session_id: str, ref: dict[str, Any]) -> dict[str, Any]:
        entry = self._get(session_id, self._ref_id(ref))
        if entry.kind != "image":
            raise ValueError("Artifact is not an image")
        current_size = entry.path.stat().st_size
        if current_size != entry.size or current_size > self._max_bytes:
            raise OSError(f"Artifact size changed before hydration: {entry.artifact_id}")
        with entry.path.open("rb") as stream:
            data = stream.read(self._max_bytes + 1)
        if len(data) != current_size:
            raise OSError(f"Artifact size changed while hydrating: {entry.artifact_id}")
        encoded = base64.b64encode(data).decode("ascii")
        return {
            "type": "image",
            "image_url": f"data:{entry.media_type};base64,{encoded}",
            "media_type": entry.media_type,
            "width": entry.width,
            "height": entry.height,
        }

    def metadata(self, session_id: str, artifact_id: str) -> dict[str, Any]:
        entry = self._get(session_id, artifact_id)
        return {
            "artifact_id": entry.artifact_id,
            "kind": entry.kind,
            "name": entry.name,
            "media_type": entry.media_type,
            "size": entry.size,
            "width": entry.width,
            "height": entry.height,
            "content_sha256": entry.content_sha256,
            "direction": entry.direction,
            "ownership": entry.ownership,
            "retention": entry.retention,
        }

    def reference(self, session_id: str, artifact_id: str, *, caption: str = "") -> dict[str, Any]:
        entry = self._get(session_id, artifact_id)
        return self._inbound_ref(entry, source="service-upload", caption=caption)

    def hydrate_messages(
        self, session_id: str, messages: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        hydrated = copy.deepcopy(messages)
        latest_user_index = max(
            (index for index, message in enumerate(hydrated) if message.get("role") == "user"),
            default=0,
        )
        for index, message in enumerate(hydrated):
            content = message.get("content")
            if not isinstance(content, list):
                continue
            resolved: list[dict[str, Any]] = []
            for block in content:
                if not isinstance(block, dict):
                    resolved.append(block)
                elif block.get("type") == "image_ref" and index >= latest_user_index:
                    resolved.append(self.hydrate_ref(session_id, block))
                elif block.get("type") == "image_ref":
                    resolved.append({
                        "type": "text",
                        "text": f"[historical image reference: {self._ref_id(block)}]",
                    })
                elif block.get("type") == "file_ref":
                    resolved.append(
                        {
                            "type": "text",
                            "text": self._file_manifest(session_id, block),
                        }
                    )
                else:
                    resolved.append(block)
            message["content"] = resolved
        return hydrated

    def manifest_messages(
        self, session_id: str, messages: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        manifested = copy.deepcopy(messages)
        for message in manifested:
            content = message.get("content")
            if not isinstance(content, list):
                continue
            resolved: list[dict[str, Any]] = []
            for block in content:
                if not isinstance(block, dict):
                    resolved.append(block)
                    continue
                if block.get("type") == "file_ref":
                    resolved.append(
                        {
                            "type": "text",
                            "text": self._file_manifest(session_id, block),
                        }
                    )
                    continue
                if block.get("type") != "image_ref":
                    resolved.append(block)
                    continue
                artifact_id = self._ref_id(block)
                try:
                    metadata = self.metadata(session_id, artifact_id)
                except KeyError:
                    metadata = {
                        "artifact_id": artifact_id,
                        "media_type": block.get("media_type", "unknown"),
                        "width": block.get("width", 0),
                        "height": block.get("height", 0),
                    }
                source = str(block.get("source", "unknown"))
                caption = str(block.get("caption", "")).strip()
                line = (
                    "[available image: "
                    f"image_id={metadata['artifact_id']}; "
                    f"media_type={metadata['media_type']}; "
                    f"size={metadata['width']}x{metadata['height']}; source={source}"
                )
                if caption:
                    line += f"; caption={caption}"
                resolved.append({"type": "text", "text": line + "]"})
            message["content"] = resolved
        return manifested

    def _file_manifest(self, session_id: str, block: dict[str, Any]) -> str:
        artifact_id = self._ref_id(block)
        try:
            metadata = self.metadata(session_id, artifact_id)
        except KeyError:
            metadata = {
                "artifact_id": artifact_id,
                "name": block.get("name", "file"),
                "media_type": block.get("media_type", "unknown"),
                "size": block.get("size", 0),
            }
        caption = str(block.get("caption", "")).strip()
        line = (
            "[available file: "
            f"artifact_id={metadata['artifact_id']}; "
            f"name={metadata['name']}; "
            f"media_type={metadata['media_type']}; "
            f"size={metadata['size']} bytes"
        )
        if caption:
            line += f"; caption={caption}"
        return line + "; use read_artifact to inspect text content]"

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def cleanup_session(self, session_id: str) -> None:
        session_key = self._session_key(session_id)
        for entry in list(self._entries.values()):
            if entry.session_key == session_key and entry.retention == "session":
                self._discard(entry)

    def cleanup_expired(self) -> None:
        now = self._clock()
        for entry in list(self._entries.values()):
            if entry.expires_at is not None and entry.expires_at <= now:
                self._discard(entry)

    def _discard(self, entry: _Artifact) -> None:
        self._entries.pop(entry.artifact_id, None)
        self._delete_registry(entry.artifact_id)
        if entry.ownership == "borrowed" or entry.retention == "persistent":
            return
        try:
            entry.path.unlink(missing_ok=True)
        except OSError:
            return
        parent = entry.path.parent
        while parent != self.root and self.root in parent.parents:
            try:
                parent.rmdir()
            except OSError:
                break
            parent = parent.parent
