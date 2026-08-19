from __future__ import annotations

import hashlib
import sqlite3
import zipfile
from pathlib import Path

from knoa_platform.hub.admin import (
    _backup,
    _mobile_latest,
    _mobile_publish,
    _mobile_upload,
    _restore,
)
from knoa_platform.hub.hosted import HostedHubApplication
from knoa_platform.mobile_releases import AndroidReleaseRepository


def _apk(path: Path, payload: bytes = b"classes") -> None:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("AndroidManifest.xml", b"manifest")
        archive.writestr("classes.dex", payload)


def test_hosted_backup_and_restore_include_control_identity_and_all_workspaces(
    tmp_path: Path,
) -> None:
    root = tmp_path / "hosted"
    application = HostedHubApplication(
        root,
        hub_id="hub_hosted",
        bootstrap_token="bootstrap-" + "b" * 40,
    )
    first_grant = application.control.create_account_enrollment_grant()
    first = application.control.create_account(
        grant_id=first_grant.grant_id,
        grant_secret=first_grant.secret,
        login_identity="owner@example.com",
        display_name="Owner",
        password="correct horse battery staple",
    )
    shared = application.control.create_workspace(
        first["account_id"],
        "Shared Workspace",
    )
    application.tenants.application(first["default_workspace_id"])
    application.tenants.application(shared["workspace_id"])
    apk = tmp_path / "knoa.apk"
    _apk(apk)
    releases = AndroidReleaseRepository(root / "mobile-releases" / "android")
    published = releases.publish(apk, version_name="0.2.46", version_code=57)

    backup = tmp_path / "backup"
    restored = tmp_path / "restored"
    assert _backup(root, backup) == 0
    assert _restore(backup, restored) == 0

    assert (restored / "hub-signing.key").read_bytes() == (
        root / "hub-signing.key"
    ).read_bytes()
    assert (restored / "hub-signing.key").stat().st_mode & 0o077 == 0
    with sqlite3.connect(restored / "control.db") as db:
        assert db.execute("SELECT COUNT(*) FROM hosted_accounts").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM hosted_workspaces").fetchone()[0] == 2
    assert {path.parent.name for path in (restored / "tenants").glob("*/hub.db")} == {
        first["default_workspace_id"],
        shared["workspace_id"],
    }
    restored_releases = AndroidReleaseRepository(
        restored / "mobile-releases" / "android"
    )
    assert restored_releases.latest() == published
    assert restored_releases.package_path(published).read_bytes() == apk.read_bytes()


def test_hosted_mobile_publish_reads_apk_metadata_and_enforces_monotonic_versions(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    root = tmp_path / "hosted"
    apk = tmp_path / "knoa.apk"
    _apk(apk)
    monkeypatch.setattr(
        "knoa_platform.hub.admin.read_apk_version",
        lambda _path: ("0.2.46", 57),
    )

    assert _mobile_publish(root, apk, min_version_code=1, notes="Hosted update") == 0
    assert _mobile_latest(root) == 0
    assert _mobile_publish(root, apk, min_version_code=1, notes="duplicate") == 2

    output = capsys.readouterr()
    assert "version_code=57" in output.out
    assert "download_path=/downloads/android/latest.apk" in output.out
    assert "increase monotonically" in output.err


def test_hosted_mobile_publish_accepts_explicit_metadata_without_android_sdk(
    tmp_path: Path,
    monkeypatch,
) -> None:
    root = tmp_path / "hosted"
    apk = tmp_path / "knoa.apk"
    _apk(apk)
    monkeypatch.setattr(
        "knoa_platform.hub.admin.read_apk_version",
        lambda _path: (_ for _ in ()).throw(AssertionError("aapt must not run")),
    )

    assert (
        _mobile_publish(
            root,
            apk,
            min_version_code=1,
            notes="Windows publish",
            version_name="0.2.53",
            version_code=64,
        )
        == 0
    )


def test_hosted_mobile_upload_streams_declared_release_to_remote_hub(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    apk = tmp_path / "knoa.apk"
    _apk(apk, b"remote")
    token = tmp_path / "publisher.token"
    token.write_text("release-" + "r" * 40, encoding="ascii")
    token.chmod(0o600)
    digest = hashlib.sha256(apk.read_bytes()).hexdigest()

    class Response:
        status_code = 201
        text = ""

        @staticmethod
        def json():
            return {
                "version_name": "0.2.53",
                "version_code": 64,
                "size_bytes": apk.stat().st_size,
                "sha256": digest,
            }

    class Client:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        @staticmethod
        def put(_endpoint, *, headers, content):
            assert headers["X-Knoa-Version-Code"] == "64"
            assert hashlib.sha256(b"".join(content)).hexdigest() == digest
            return Response()

    monkeypatch.setattr(
        "knoa_platform.hub.admin.read_apk_version", lambda _path: ("0.2.53", 64)
    )
    monkeypatch.setattr("knoa_platform.hub.admin.httpx.Client", Client)

    assert (
        _mobile_upload(
            apk,
            hub_url="https://hub.example.com",
            token_file=token,
            min_version_code=1,
            notes="Remote update",
        )
        == 0
    )
    assert (
        "download_url=https://hub.example.com/downloads/android/latest.apk"
        in capsys.readouterr().out
    )


def test_hosted_restore_refuses_non_empty_root(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    (root / "keep").write_text("user data", encoding="utf-8")

    assert _restore(tmp_path / "missing", root) == 2
    assert (root / "keep").read_text(encoding="utf-8") == "user data"
