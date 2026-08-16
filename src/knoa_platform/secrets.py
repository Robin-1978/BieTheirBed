"""Write-only owner secret store with non-sensitive status metadata."""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import time
from pathlib import Path

_REFERENCE = re.compile(r"^[a-z][a-z0-9_.-]{0,127}$")


class SecretStore:
    def __init__(self, root: str | Path, *, clock=time.time) -> None:
        self.root = Path(root).expanduser().resolve()
        self._clock = clock

    def put(self, reference: str, value: str) -> dict[str, object]:
        normalized = self._reference(reference)
        if not value or len(value.encode("utf-8")) > 64 * 1024 or "\x00" in value:
            raise ValueError("Secret must contain 1-65536 safe bytes")
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        payload_path = self.root / normalized
        metadata_path = self.root / f".{normalized}.json"
        temporary = self.root / f".{normalized}.{secrets.token_hex(8)}.tmp"
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                stream.write(value)
                stream.flush()
                os.fsync(stream.fileno())
            temporary.replace(payload_path)
            payload_path.chmod(0o600)
        finally:
            temporary.unlink(missing_ok=True)
        metadata = {
            "reference": normalized,
            "configured": True,
            "rotated_at": float(self._clock()),
            "fingerprint": hashlib.sha256(value.encode()).hexdigest()[:12],
        }
        metadata_temporary = self.root / f".{normalized}.{secrets.token_hex(8)}.metadata.tmp"
        descriptor = os.open(
            metadata_temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump(metadata, stream, sort_keys=True, separators=(",", ":"))
                stream.flush()
                os.fsync(stream.fileno())
            metadata_temporary.replace(metadata_path)
            metadata_path.chmod(0o600)
        finally:
            metadata_temporary.unlink(missing_ok=True)
        directory = os.open(self.root, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
        return metadata

    def get(self, reference: str) -> str:
        path = self.root / self._reference(reference)
        if path.is_symlink() or not path.is_file() or path.stat().st_mode & 0o077:
            raise LookupError("Secret is not configured")
        return path.read_text(encoding="utf-8")

    def status(self, reference: str) -> dict[str, object]:
        normalized = self._reference(reference)
        path = self.root / f".{normalized}.json"
        if not path.is_file():
            return {"reference": normalized, "configured": False, "rotated_at": 0}
        raw = json.loads(path.read_text(encoding="utf-8"))
        return {
            "reference": normalized,
            "configured": bool(raw.get("configured")),
            "rotated_at": float(raw.get("rotated_at", 0)),
            "fingerprint": str(raw.get("fingerprint", "")),
        }

    @staticmethod
    def _reference(value: str) -> str:
        normalized = value.strip()
        if not _REFERENCE.fullmatch(normalized):
            raise ValueError("Secret reference must use a safe lowercase identifier")
        return normalized


__all__ = ["SecretStore"]
