from __future__ import annotations

import os
import shutil
import stat
import zipfile
from pathlib import Path, PurePosixPath

_MAX_FILES = 20_000
_MAX_FILE_BYTES = 2 * 1024 * 1024 * 1024
_MAX_TOTAL_BYTES = 8 * 1024 * 1024 * 1024
_COPY_BUFFER_BYTES = 1024 * 1024


def _safe_archive_path(value: str) -> PurePosixPath:
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or value != path.as_posix()
        or any(part in {"", ".", ".."} for part in path.parts)
        or "\\" in value
        or ":" in value
    ):
        raise ValueError("Release archive contains an unsafe path")
    return path


def pack_bundle(bundle_root: Path, archive_path: Path) -> None:
    if not bundle_root.is_dir():
        raise ValueError("Release bundle directory is missing")
    if archive_path.exists():
        raise FileExistsError("Release archive already exists")
    files = sorted(candidate for candidate in bundle_root.rglob("*") if candidate.is_file())
    if len(files) > _MAX_FILES:
        raise ValueError("Release bundle contains too many files")
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(
        archive_path,
        mode="x",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
    ) as archive:
        for candidate in files:
            if candidate.is_symlink():
                raise ValueError("Release bundle cannot contain symlinks")
            relative = candidate.relative_to(bundle_root).as_posix()
            info = zipfile.ZipInfo(relative, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = (candidate.stat().st_mode & 0xFFFF) << 16
            with candidate.open("rb") as source, archive.open(info, mode="w") as target:
                shutil.copyfileobj(source, target, length=_COPY_BUFFER_BYTES)


def extract_bundle(archive_path: Path, destination: Path) -> None:
    if destination.exists() and any(destination.iterdir()):
        raise ValueError("Release extraction destination must be empty")
    destination.mkdir(parents=True, exist_ok=True)
    root = destination.resolve(strict=True)
    with zipfile.ZipFile(archive_path, mode="r") as archive:
        entries = archive.infolist()
        if len(entries) > _MAX_FILES:
            raise ValueError("Release archive contains too many files")
        names: set[str] = set()
        total_size = 0
        for info in entries:
            path = _safe_archive_path(info.filename)
            if info.filename in names:
                raise ValueError("Release archive contains duplicate paths")
            names.add(info.filename)
            mode = info.external_attr >> 16
            if stat.S_ISLNK(mode):
                raise ValueError("Release archive cannot contain symlinks")
            if info.flag_bits & 0x1:
                raise ValueError("Encrypted Release archives are not supported")
            if info.file_size > _MAX_FILE_BYTES:
                raise ValueError("Release archive file exceeds size limit")
            total_size += info.file_size
            if total_size > _MAX_TOTAL_BYTES:
                raise ValueError("Release archive exceeds total size limit")
            target = destination.joinpath(*path.parts)
            resolved_parent = target.parent.resolve(strict=False)
            if resolved_parent != root and root not in resolved_parent.parents:
                raise ValueError("Release archive path escapes destination")
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info, mode="r") as source, target.open("xb") as output:
                copied = 0
                while True:
                    chunk = source.read(_COPY_BUFFER_BYTES)
                    if not chunk:
                        break
                    copied += len(chunk)
                    if copied > info.file_size:
                        raise ValueError("Release archive expanded beyond declared size")
                    output.write(chunk)
                if copied != info.file_size:
                    raise ValueError("Release archive file size mismatch")
            if os.name != "nt":
                target.chmod(mode & 0o777)


__all__ = ["extract_bundle", "pack_bundle"]
