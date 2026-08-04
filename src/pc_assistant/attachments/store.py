"""Bounded, session-scoped storage for temporary image observations.

Conversation history stores only ``image_ref`` blocks.  Binary bytes live below
the attachment root and are converted to provider data URLs only while a single
request is being assembled.
"""
from __future__ import annotations

import base64
import copy
import hashlib
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class _Attachment:
    attachment_id: str
    session_key: str
    path: Path
    media_type: str
    width: int
    height: int
    expires_at: float


class AttachmentStore:
    """Temporary image store with ownership checks and TTL cleanup."""

    def __init__(
        self,
        root: str | Path = "attachments",
        *,
        ttl_seconds: float = 3600,
        max_bytes: int = 20 * 1024 * 1024,
        clock=time.time,
    ) -> None:
        self.root = Path(root)
        self._ttl = max(1.0, ttl_seconds)
        self._max_bytes = max(1, max_bytes)
        self._clock = clock
        self._entries: dict[str, _Attachment] = {}
        self.cleanup_expired()

    @staticmethod
    def _session_key(session_id: str) -> str:
        return hashlib.sha256((session_id or "default").encode()).hexdigest()[:20]

    @staticmethod
    def _decode_data_url(data_url: str, fallback_media_type: str) -> tuple[bytes, str]:
        if not data_url.startswith("data:image/") or "," not in data_url:
            raise ValueError("Attachment must be an image data URL")
        metadata, encoded = data_url.split(",", 1)
        if ";base64" not in metadata:
            raise ValueError("Attachment data URL must use base64 encoding")
        media_type = metadata[5:].split(";", 1)[0] or fallback_media_type
        try:
            data = base64.b64decode(encoded, validate=True)
        except Exception as exc:
            raise ValueError("Attachment contains invalid base64") from exc
        return data, media_type

    @staticmethod
    def _dimensions(data: bytes) -> tuple[int, int]:
        try:
            import io
            from PIL import Image

            with Image.open(io.BytesIO(data)) as image:
                return int(image.width), int(image.height)
        except Exception:
            return 0, 0

    def put_data_url(
        self,
        session_id: str,
        data_url: str,
        *,
        media_type: str = "image/jpeg",
        source: str = "upload",
        caption: str = "",
    ) -> dict[str, Any]:
        data, detected_media = self._decode_data_url(data_url, media_type)
        if not data or len(data) > self._max_bytes:
            raise ValueError(f"Attachment size must be between 1 and {self._max_bytes} bytes")

        self.cleanup_expired()
        attachment_id = uuid.uuid4().hex
        session_key = self._session_key(session_id)
        suffix = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}.get(
            detected_media,
            ".img",
        )
        directory = self.root / session_key
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{attachment_id}{suffix}"
        temporary = directory / f".{attachment_id}.tmp"
        temporary.write_bytes(data)
        temporary.replace(path)

        width, height = self._dimensions(data)
        entry = _Attachment(
            attachment_id=attachment_id,
            session_key=session_key,
            path=path,
            media_type=detected_media,
            width=width,
            height=height,
            expires_at=self._clock() + self._ttl,
        )
        self._entries[attachment_id] = entry
        ref: dict[str, Any] = {
            "type": "image_ref",
            "attachment_id": attachment_id,
            "media_type": detected_media,
            "width": width,
            "height": height,
            "source": source,
        }
        if caption:
            ref["caption"] = caption[:200]
        return ref

    def _get(self, session_id: str, attachment_id: str) -> _Attachment:
        entry = self._entries.get(attachment_id)
        if entry is None or entry.session_key != self._session_key(session_id):
            raise KeyError(f"Attachment not found: {attachment_id}")
        if entry.expires_at <= self._clock() or not entry.path.exists():
            self._delete(entry)
            raise KeyError(f"Attachment expired: {attachment_id}")
        return entry

    def hydrate_ref(self, session_id: str, ref: dict[str, Any]) -> dict[str, Any]:
        entry = self._get(session_id, str(ref.get("attachment_id", "")))
        encoded = base64.b64encode(entry.path.read_bytes()).decode("ascii")
        return {
            "type": "image",
            "image_url": f"data:{entry.media_type};base64,{encoded}",
            "media_type": entry.media_type,
            "width": entry.width,
            "height": entry.height,
        }

    def reference(
        self,
        session_id: str,
        attachment_id: str,
        *,
        caption: str = "",
    ) -> dict[str, Any]:
        entry = self._get(session_id, attachment_id)
        ref: dict[str, Any] = {
            "type": "image_ref",
            "attachment_id": entry.attachment_id,
            "media_type": entry.media_type,
            "width": entry.width,
            "height": entry.height,
            "source": "service-upload",
        }
        if caption:
            ref["caption"] = caption[:200]
        return ref

    def hydrate_messages(
        self,
        session_id: str,
        messages: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Return a request-only hydrated copy; never mutate history."""
        hydrated = copy.deepcopy(messages)
        for message in hydrated:
            content = message.get("content")
            if not isinstance(content, list):
                continue
            message["content"] = [
                self.hydrate_ref(session_id, block)
                if isinstance(block, dict) and block.get("type") == "image_ref"
                else block
                for block in content
            ]
        return hydrated

    def cleanup_session(self, session_id: str) -> None:
        session_key = self._session_key(session_id)
        for entry in list(self._entries.values()):
            if entry.session_key == session_key:
                self._delete(entry)

    def cleanup_expired(self) -> None:
        now = self._clock()
        for entry in list(self._entries.values()):
            if entry.expires_at <= now:
                self._delete(entry)
        if not self.root.exists():
            return
        for path in self.root.rglob("*"):
            if not path.is_file():
                continue
            try:
                if now - path.stat().st_mtime > self._ttl:
                    path.unlink(missing_ok=True)
            except OSError:
                pass
        for directory in sorted(
            (path for path in self.root.rglob("*") if path.is_dir()),
            key=lambda path: len(path.parts),
            reverse=True,
        ):
            try:
                directory.rmdir()
            except OSError:
                pass

    def _delete(self, entry: _Attachment) -> None:
        self._entries.pop(entry.attachment_id, None)
        try:
            entry.path.unlink(missing_ok=True)
            entry.path.parent.rmdir()
        except OSError:
            pass
