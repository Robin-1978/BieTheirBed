from __future__ import annotations

import sqlite3
from pathlib import Path

from knoa_platform.hub.admin import _backup, _restore
from knoa_platform.hub.hosted import HostedHubApplication


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
    assert {
        path.parent.name for path in (restored / "tenants").glob("*/hub.db")
    } == {first["default_workspace_id"], shared["workspace_id"]}


def test_hosted_restore_refuses_non_empty_root(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    (root / "keep").write_text("user data", encoding="utf-8")

    assert _restore(tmp_path / "missing", root) == 2
    assert (root / "keep").read_text(encoding="utf-8") == "user data"
