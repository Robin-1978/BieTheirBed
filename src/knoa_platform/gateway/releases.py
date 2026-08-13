"""Private Android release storage owned by the Secure Gateway deployment."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import tempfile
import time
import zipfile
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError


_MAX_APK_BYTES = 1024 * 1024 * 1024
_VERSION_NAME = re.compile(r"[0-9A-Za-z][0-9A-Za-z._+-]{0,31}")


class AndroidRelease(BaseModel):
    """Immutable metadata for one locally published APK."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    version_name: str = Field(min_length=1, max_length=32)
    version_code: int = Field(ge=1, le=2_100_000_000)
    min_supported_version_code: int = Field(ge=1, le=2_100_000_000)
    size_bytes: int = Field(ge=1, le=_MAX_APK_BYTES)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    published_at: float = Field(gt=0)
    release_notes: str = Field(default="", max_length=20_000)
    file_name: str = Field(pattern=r"^knoa-[1-9][0-9]*\.apk$")


class AndroidReleaseRepository:
    """Atomically publish and resolve immutable personal Android packages."""

    def __init__(self, root: Path) -> None:
        self._root = root

    @property
    def root(self) -> Path:
        return self._root

    def publish(
        self,
        apk_path: Path,
        *,
        version_name: str,
        version_code: int,
        min_supported_version_code: int = 1,
        release_notes: str = "",
        clock=time.time,
    ) -> AndroidRelease:
        version_name = version_name.strip()
        if not _VERSION_NAME.fullmatch(version_name):
            raise ValueError("Android version name must contain 1-32 safe characters")
        if version_code < 1 or version_code > 2_100_000_000:
            raise ValueError("Android version code must be between 1 and 2100000000")
        if min_supported_version_code < 1 or min_supported_version_code > version_code:
            raise ValueError(
                "Minimum supported version code must be between 1 and version code"
            )
        if len(release_notes) > 20_000:
            raise ValueError("Android release notes are too long")

        source = apk_path.expanduser()
        try:
            metadata = source.lstat()
        except OSError as exc:
            raise ValueError("Android APK is unavailable") from exc
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
            raise ValueError("Android APK must be a regular non-symlink file")
        if metadata.st_size <= 0 or metadata.st_size > _MAX_APK_BYTES:
            raise ValueError("Android APK must contain between 1 byte and 1 GiB")
        try:
            with zipfile.ZipFile(source) as archive:
                if "AndroidManifest.xml" not in archive.namelist():
                    raise ValueError("Android APK does not contain AndroidManifest.xml")
        except zipfile.BadZipFile as exc:
            raise ValueError("Android APK must be a valid APK archive") from exc

        current = self.latest(optional=True)
        if current is not None and version_code <= current.version_code:
            raise ValueError("Android version code must increase monotonically")

        self._ensure_root()
        file_name = f"knoa-{version_code}.apk"
        destination = self._root / file_name
        if destination.exists():
            raise ValueError("Android version code has already been published")

        temporary_apk = self._temporary_path(".apk.tmp")
        digest = hashlib.sha256()
        size_bytes = 0
        try:
            with source.open("rb") as incoming, temporary_apk.open("xb") as outgoing:
                while chunk := incoming.read(1024 * 1024):
                    size_bytes += len(chunk)
                    if size_bytes > _MAX_APK_BYTES:
                        raise ValueError(
                            "Android APK must contain between 1 byte and 1 GiB"
                        )
                    digest.update(chunk)
                    outgoing.write(chunk)
                if size_bytes <= 0:
                    raise ValueError("Android APK must contain between 1 byte and 1 GiB")
                outgoing.flush()
                os.fsync(outgoing.fileno())
            temporary_apk.chmod(0o600)
            os.replace(temporary_apk, destination)
        finally:
            temporary_apk.unlink(missing_ok=True)

        release = AndroidRelease(
            version_name=version_name,
            version_code=version_code,
            min_supported_version_code=min_supported_version_code,
            size_bytes=size_bytes,
            sha256=digest.hexdigest(),
            published_at=float(clock()),
            release_notes=release_notes,
            file_name=file_name,
        )
        version_manifest = self._manifest_path(version_code)
        try:
            self._write_manifest(version_manifest, release)
            self._write_manifest(self._root / "latest.json", release)
        except BaseException:
            destination.unlink(missing_ok=True)
            version_manifest.unlink(missing_ok=True)
            raise
        return release

    def latest(self, *, optional: bool = False) -> AndroidRelease | None:
        return self._read_manifest(self._root / "latest.json", optional=optional)

    def get(self, version_code: int) -> AndroidRelease:
        release = self._read_manifest(self._manifest_path(version_code), optional=False)
        assert release is not None
        return release

    def package_path(self, release: AndroidRelease) -> Path:
        candidate = self._root / release.file_name
        try:
            metadata = candidate.lstat()
        except OSError as exc:
            raise LookupError("Android release package is unavailable") from exc
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
            raise LookupError("Android release package is unavailable")
        if metadata.st_size != release.size_bytes:
            raise LookupError(
                "Android release package size does not match its manifest"
            )
        return candidate

    def _read_manifest(self, path: Path, *, optional: bool) -> AndroidRelease | None:
        try:
            raw = path.read_bytes()
        except FileNotFoundError:
            if optional:
                return None
            raise LookupError("Android release was not found") from None
        except OSError as exc:
            raise LookupError("Android release manifest is unavailable") from exc
        if len(raw) > 64 * 1024:
            raise LookupError("Android release manifest is invalid")
        try:
            return AndroidRelease.model_validate_json(raw)
        except ValidationError as exc:
            raise LookupError("Android release manifest is invalid") from exc

    def _write_manifest(self, path: Path, release: AndroidRelease) -> None:
        temporary = self._temporary_path(".json.tmp")
        try:
            with temporary.open("x", encoding="utf-8") as handle:
                json.dump(
                    release.model_dump(mode="json"),
                    handle,
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            temporary.chmod(0o600)
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    def _ensure_root(self) -> None:
        self._root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._root.chmod(0o700)

    def _manifest_path(self, version_code: int) -> Path:
        return self._root / f"{version_code}.json"

    def _temporary_path(self, suffix: str) -> Path:
        descriptor, value = tempfile.mkstemp(
            prefix=".publish-", suffix=suffix, dir=self._root
        )
        os.close(descriptor)
        path = Path(value)
        path.unlink()
        return path
