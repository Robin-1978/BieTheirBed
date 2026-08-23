"""Immutable content-addressed storage for Skill and MCP data packages."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import shutil
import stat
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

PackageKind = Literal["skill", "mcp", "capability"]
_MAX_FILES = 4096
_MAX_FILE_BYTES = 32 * 1024 * 1024
_MAX_PACKAGE_BYTES = 128 * 1024 * 1024
_METADATA = ".knoa-package.json"


@dataclass(frozen=True)
class PackageRecord:
    package_id: str
    kind: PackageKind
    content_digest: str
    path: Path
    source_type: str
    source_locator: str
    imported_by: str
    imported_at: float
    file_count: int
    size_bytes: int

    def public_dict(self) -> dict[str, object]:
        return {
            "package_id": self.package_id,
            "kind": self.kind,
            "content_digest": self.content_digest,
            "source_type": self.source_type,
            "source_locator": self.source_locator,
            "imported_by": self.imported_by,
            "imported_at": self.imported_at,
            "file_count": self.file_count,
            "size_bytes": self.size_bytes,
        }


class PackageStore:
    """Own package bytes only; Config Revision remains activation authority."""

    def __init__(self, root: str | Path, *, clock=time.time) -> None:
        self.root = Path(root).expanduser().resolve()
        self._clock = clock

    def import_directory(
        self,
        kind: PackageKind,
        source: str | Path,
        *,
        source_type: str = "local_directory",
        source_locator: str = "",
        imported_by: str,
    ) -> PackageRecord:
        source_path = Path(source).expanduser().resolve()
        if not source_path.is_dir():
            raise ValueError("Package import source must be a directory")
        try:
            self.root.relative_to(source_path)
        except ValueError:
            pass
        else:
            raise ValueError("Package source must not contain the managed PackageStore")
        entries, content_digest, total_bytes = self._inventory(source_path)
        package_id = f"{kind}-{content_digest}"
        target = self.root / kind / package_id
        metadata = {
            "package_id": package_id,
            "kind": kind,
            "content_digest": content_digest,
            "source_type": source_type[:64],
            "source_locator": (source_locator or str(source_path))[:4096],
            "imported_by": imported_by[:256],
            "imported_at": float(self._clock()),
            "file_count": len(entries),
            "size_bytes": total_bytes,
            "payload_directory": source_path.name,
        }
        if target.exists():
            return self.get(package_id)
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        staging_root = self.root / ".staging"
        staging_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        stage = staging_root / secrets.token_hex(16)
        stage.mkdir(mode=0o700)
        try:
            payload = stage / source_path.name
            payload.mkdir(mode=0o700)
            for source_file, relative, _size in entries:
                destination = payload / relative
                destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                shutil.copyfile(source_file, destination, follow_symlinks=False)
                destination.chmod(0o600)
            (stage / _METADATA).write_text(
                json.dumps(metadata, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                encoding="utf-8",
            )
            (stage / _METADATA).chmod(0o600)
            try:
                stage.replace(target)
            except OSError:
                if not target.exists():
                    raise
        finally:
            shutil.rmtree(stage, ignore_errors=True)
        return self.get(package_id)

    def get(self, package_id: str, *, expected_kind: PackageKind | None = None) -> PackageRecord:
        kind = self._kind_from_id(package_id)
        if expected_kind is not None and kind != expected_kind:
            raise LookupError("Package kind does not match")
        storage_path = self.root / kind / package_id
        metadata_path = storage_path / _METADATA
        if storage_path.is_symlink() or not storage_path.is_dir() or not metadata_path.is_file():
            raise LookupError("Package not found")
        raw = json.loads(metadata_path.read_text(encoding="utf-8"))
        if raw.get("package_id") != package_id or raw.get("kind") != kind:
            raise ValueError("Package metadata is inconsistent")
        payload_directory = str(raw.get("payload_directory", ""))
        if not payload_directory or Path(payload_directory).name != payload_directory:
            raise ValueError("Package payload directory is invalid")
        path = storage_path / payload_directory
        if path.is_symlink() or not path.is_dir():
            raise ValueError("Package payload is missing")
        _entries, digest, total_bytes = self._inventory(path)
        if digest != raw.get("content_digest") or total_bytes != int(raw.get("size_bytes", -1)):
            raise ValueError("Package content digest mismatch")
        return PackageRecord(
            package_id=package_id,
            kind=kind,
            content_digest=digest,
            path=path,
            source_type=str(raw.get("source_type", "")),
            source_locator=str(raw.get("source_locator", "")),
            imported_by=str(raw.get("imported_by", "")),
            imported_at=float(raw.get("imported_at", 0)),
            file_count=int(raw.get("file_count", 0)),
            size_bytes=total_bytes,
        )

    def list(self) -> tuple[PackageRecord, ...]:
        records: list[PackageRecord] = []
        for kind in ("skill", "mcp", "capability"):
            parent = self.root / kind
            if not parent.is_dir():
                continue
            for path in sorted(parent.iterdir()):
                if path.is_dir():
                    records.append(self.get(path.name, expected_kind=kind))
        return tuple(records)

    @staticmethod
    def _kind_from_id(package_id: str) -> PackageKind:
        if package_id.startswith("skill-"):
            kind: PackageKind = "skill"
        elif package_id.startswith("mcp-"):
            kind = "mcp"
        elif package_id.startswith("capability-"):
            kind = "capability"
        else:
            raise LookupError("Package ID is invalid")
        digest = package_id.removeprefix(f"{kind}-")
        if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
            raise LookupError("Package ID is invalid")
        return kind

    @staticmethod
    def _inventory(
        root: Path,
        *,
        exclude_metadata: bool = False,
    ) -> tuple[tuple[tuple[Path, Path, int], ...], str, int]:
        entries: list[tuple[Path, Path, int]] = []
        total_bytes = 0
        for current, directories, names in os.walk(root, followlinks=False):
            current_path = Path(current)
            for name in tuple(directories):
                candidate = current_path / name
                if candidate.is_symlink():
                    raise ValueError("Packages must not contain symlinks")
            for name in names:
                if exclude_metadata and current_path == root and name == _METADATA:
                    continue
                candidate = current_path / name
                metadata = candidate.lstat()
                if not stat.S_ISREG(metadata.st_mode):
                    raise ValueError("Packages may contain only regular files")
                if metadata.st_size > _MAX_FILE_BYTES:
                    raise ValueError("Package contains an oversized file")
                total_bytes += metadata.st_size
                if total_bytes > _MAX_PACKAGE_BYTES:
                    raise ValueError("Package exceeds total size limit")
                relative = candidate.relative_to(root)
                if any(part in {"", ".", ".."} for part in relative.parts):
                    raise ValueError("Package contains an unsafe path")
                entries.append((candidate, relative, metadata.st_size))
                if len(entries) > _MAX_FILES:
                    raise ValueError("Package contains too many files")
        digest = hashlib.sha256()
        for source, relative, size in sorted(entries, key=lambda item: item[1].as_posix()):
            encoded = relative.as_posix().encode("utf-8")
            digest.update(len(encoded).to_bytes(4, "big"))
            digest.update(encoded)
            digest.update(size.to_bytes(8, "big"))
            with source.open("rb") as stream:
                while chunk := stream.read(1024 * 1024):
                    digest.update(chunk)
        return tuple(entries), digest.hexdigest(), total_bytes


__all__ = ["PackageKind", "PackageRecord", "PackageStore"]
