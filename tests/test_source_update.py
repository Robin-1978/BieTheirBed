from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from knoa_platform.source_update import SourceUpdateError, SourceUpdateManager


def _git(root: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", *arguments],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _repository(tmp_path: Path) -> tuple[Path, Path]:
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True)
    author = tmp_path / "author"
    subprocess.run(["git", "clone", str(remote), str(author)], check=True, capture_output=True)
    _git(author, "config", "user.email", "test@example.test")
    _git(author, "config", "user.name", "Knoa Test")
    (author / "pyproject.toml").write_text("[project]\nname='knoa-test'\nversion='1'\n", encoding="utf-8")
    _git(author, "add", "pyproject.toml")
    _git(author, "commit", "-m", "first")
    _git(author, "push", "-u", "origin", "HEAD")
    channel = tmp_path / "channel"
    subprocess.run(["git", "clone", str(remote), str(channel)], check=True, capture_output=True)
    return author, channel


def _manager(tmp_path: Path, channel: Path) -> SourceUpdateManager:
    installation = tmp_path / "installation.json"
    installation.write_text(
        json.dumps({"schema_version": 1, "role": "all"}),
        encoding="utf-8",
    )
    return SourceUpdateManager(
        source_root=channel,
        state_file=tmp_path / "state.json",
        snapshots_root=tmp_path / "snapshots",
        installation_state_file=installation,
    )


def test_source_channel_checks_and_installs_updates(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    author, channel = _repository(tmp_path)
    manager = _manager(tmp_path, channel)
    first = _git(channel, "rev-parse", "HEAD")

    assert manager.check()["update_available"] is False

    (author / "feature.txt").write_text("second\n", encoding="utf-8")
    _git(author, "add", "feature.txt")
    _git(author, "commit", "-m", "second")
    _git(author, "push")
    second = _git(author, "rev-parse", "HEAD")

    checked = manager.check()
    assert checked["current_commit"] == first
    assert checked["latest_commit"] == second
    assert checked["update_available"] is True

    installed: list[str] = []

    def record_install(snapshot: Path, installation: dict) -> None:
        assert installation["role"] == "all"
        installed.append(_git(snapshot, "rev-parse", "HEAD"))

    monkeypatch.setattr(manager, "_install", record_install)
    updated = manager.update()
    assert installed == [second]
    assert updated["current_commit"] == second
    assert _git(channel, "rev-parse", "HEAD") == second


def test_source_channel_refuses_tracked_checkout_changes(tmp_path: Path) -> None:
    _author, channel = _repository(tmp_path)
    manager = _manager(tmp_path, channel)
    (channel / "pyproject.toml").write_text("changed\n", encoding="utf-8")

    with pytest.raises(SourceUpdateError, match="tracked_changes"):
        manager.check()


def test_source_channel_automatically_restores_previous_commit_on_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    author, channel = _repository(tmp_path)
    manager = _manager(tmp_path, channel)
    first = _git(channel, "rev-parse", "HEAD")
    (author / "broken.txt").write_text("broken\n", encoding="utf-8")
    _git(author, "add", "broken.txt")
    _git(author, "commit", "-m", "broken")
    _git(author, "push")
    second = _git(author, "rev-parse", "HEAD")
    attempts: list[str] = []

    def fail_then_restore(snapshot: Path, _installation: dict) -> None:
        commit = _git(snapshot, "rev-parse", "HEAD")
        attempts.append(commit)
        if commit == second:
            raise SourceUpdateError("simulated_install_failure")

    monkeypatch.setattr(manager, "_install", fail_then_restore)

    with pytest.raises(SourceUpdateError, match="simulated_install_failure"):
        manager.update()

    assert attempts == [second, first]


def test_source_status_uses_installed_revision_not_mutable_checkout(tmp_path: Path) -> None:
    author, channel = _repository(tmp_path)
    manager = _manager(tmp_path, channel)
    installed = _git(channel, "rev-parse", "HEAD")
    installation = manager.installation_state_file
    installation.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "role": "all",
                "installed_commit": installed,
            }
        ),
        encoding="utf-8",
    )

    (author / "next.txt").write_text("next\n", encoding="utf-8")
    _git(author, "add", "next.txt")
    _git(author, "commit", "-m", "next")
    _git(author, "push")
    _git(channel, "pull", "--ff-only")

    assert _git(channel, "rev-parse", "HEAD") != installed
    assert manager.status()["current_commit"] == installed
