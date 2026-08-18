from __future__ import annotations

import os
import stat

import pytest

from knoa_platform import private_files


def test_prepare_private_file_creates_private_regular_file(tmp_path) -> None:
    path = private_files.prepare_private_file(
        tmp_path / "state" / "token",
        label="Test token",
    )

    assert path.is_file()
    assert not path.is_symlink()
    if os.name != "nt":
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700


def test_validate_private_file_rejects_symlink(tmp_path) -> None:
    target = tmp_path / "target"
    target.write_text("secret", encoding="utf-8")
    link = tmp_path / "link"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("The test account cannot create symlinks")

    with pytest.raises(RuntimeError, match="non-symlink"):
        private_files.validate_private_file(link, label="Test token")


def test_windows_validation_defers_acl_enforcement_to_installer(
    tmp_path,
    monkeypatch,
) -> None:
    path = tmp_path / "token"
    path.write_text("secret", encoding="utf-8")
    path.chmod(0o644)
    monkeypatch.setattr(private_files, "IS_WINDOWS", True)

    assert private_files.validate_private_file(path, label="Test token") == path
    private_files.fsync_directory(tmp_path)

