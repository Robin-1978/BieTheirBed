from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from knoa_platform.release import extract_bundle, pack_bundle


def test_release_archive_round_trip_is_deterministic(tmp_path: Path) -> None:
    bundle = tmp_path / "bundle"
    (bundle / "bin").mkdir(parents=True)
    launcher = bundle / "bin" / "knoa"
    launcher.write_bytes(b"launcher")
    launcher.chmod(0o755)
    (bundle / "release-manifest.json").write_text("{}\n", encoding="utf-8")
    first = tmp_path / "first.zip"
    second = tmp_path / "second.zip"

    pack_bundle(bundle, first)
    pack_bundle(bundle, second)

    assert first.read_bytes() == second.read_bytes()
    extracted = tmp_path / "extracted"
    extract_bundle(first, extracted)
    assert (extracted / "bin" / "knoa").read_bytes() == b"launcher"
    assert (extracted / "release-manifest.json").read_text(encoding="utf-8") == "{}\n"


def test_release_archive_rejects_path_traversal(tmp_path: Path) -> None:
    archive_path = tmp_path / "malicious.zip"
    with zipfile.ZipFile(archive_path, mode="w") as archive:
        archive.writestr("../outside", b"owned")

    with pytest.raises(ValueError, match="unsafe path"):
        extract_bundle(archive_path, tmp_path / "output")
    assert not (tmp_path / "outside").exists()


def test_release_archive_does_not_overwrite_destination(tmp_path: Path) -> None:
    archive_path = tmp_path / "bundle.zip"
    with zipfile.ZipFile(archive_path, mode="w") as archive:
        archive.writestr("release-manifest.json", b"{}")
    destination = tmp_path / "output"
    destination.mkdir()
    (destination / "user-file").write_text("keep", encoding="utf-8")

    with pytest.raises(ValueError, match="must be empty"):
        extract_bundle(archive_path, destination)
    assert (destination / "user-file").read_text(encoding="utf-8") == "keep"
