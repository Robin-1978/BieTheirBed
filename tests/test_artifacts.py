from __future__ import annotations

import sqlite3

import pytest

from knoa_platform.artifacts import ArtifactStore
from knoa_platform.tools.screenshot import ScreenshotTool


def test_artifact_store_borrows_existing_file_and_never_deletes_source(tmp_path):
    source = tmp_path / "report.txt"
    source.write_text("hello", encoding="utf-8")
    store = ArtifactStore(tmp_path / "attachments", ttl_seconds=10, clock=lambda: 100.0)

    ref = store.prepare_path("session-a", source)

    assert ref["artifact_id"]
    assert ref["name"] == "report.txt"
    assert ref["media_type"] == "text/plain"
    assert ref["ownership"] == "borrowed"
    assert "path" not in ref
    assert store.public_ref("session-a", ref["artifact_id"])["name"] == "report.txt"

    store.cleanup_session("session-a")
    assert source.read_text(encoding="utf-8") == "hello"

    with pytest.raises(KeyError):
        store.public_ref("session-b", ref["artifact_id"])


def test_artifact_store_rejects_unscoped_session(tmp_path):
    source = tmp_path / "report.txt"
    source.write_text("hello", encoding="utf-8")
    store = ArtifactStore(tmp_path / "attachments")

    with pytest.raises(ValueError, match="session ID"):
        store.prepare_path("", source)


def test_borrowed_file_survives_artifact_expiry(tmp_path):
    now = [100.0]
    source = tmp_path / "report.txt"
    source.write_text("keep me", encoding="utf-8")
    store = ArtifactStore(
        tmp_path / "attachments",
        ttl_seconds=10,
        clock=lambda: now[0],
    )
    ref = store.prepare_path("session-a", source)

    now[0] = 111.0
    store.cleanup_expired()

    assert source.read_text(encoding="utf-8") == "keep me"
    with pytest.raises(KeyError):
        store.public_ref("session-a", ref["artifact_id"])


def test_registering_artifact_collects_expired_managed_entries(tmp_path):
    now = [100.0]
    root = tmp_path / "attachments"
    first = root / "screenshots" / "first.png"
    second = root / "screenshots" / "second.png"
    first.parent.mkdir(parents=True)
    first.write_bytes(b"first")
    second.write_bytes(b"second")
    store = ArtifactStore(root, ttl_seconds=10, clock=lambda: now[0])
    store.register_generated("session-a", first, media_type="image/png")

    now[0] = 111.0
    store.register_generated("session-a", second, media_type="image/png")

    assert not first.exists()
    assert second.exists()


def test_incompatible_artifact_registry_requires_offline_migration(tmp_path):
    db_path = tmp_path / "assistant.db"
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "CREATE TABLE artifact_registry (artifact_id TEXT PRIMARY KEY)"
        )

    with pytest.raises(RuntimeError, match="explicit offline migration"):
        ArtifactStore(tmp_path / "attachments", db_path=db_path)


def test_delivered_temporary_generated_artifact_uses_grace_period(tmp_path):
    now = [100.0]
    root = tmp_path / "attachments"
    generated = root / "screenshots" / "capture.png"
    generated.parent.mkdir(parents=True)
    generated.write_bytes(b"png")
    store = ArtifactStore(
        root,
        ttl_seconds=100,
        delivery_grace_seconds=5,
        clock=lambda: now[0],
    )
    ref = store.register_generated("session-a", generated, media_type="image/png")

    delivered = store.mark_delivered("session-a", ref["artifact_id"])
    assert delivered["status"] == "delivered"
    now[0] = 106.0
    store.cleanup_expired()

    assert not generated.exists()


def test_artifact_download_rejects_file_growth_before_read(tmp_path):
    source = tmp_path / "report.txt"
    source.write_bytes(b"small")
    store = ArtifactStore(tmp_path / "attachments", max_bytes=10)
    ref = store.prepare_path("session-a", source)
    source.write_bytes(b"larger-than-registered")

    with pytest.raises(OSError, match="changed before delivery"):
        store.read_data_url(
            "session-a",
            ref["artifact_id"],
            max_bytes=10,
        )


def test_persistent_generated_artifact_survives_session_and_store_restart(tmp_path):
    root = tmp_path / "attachments"
    persistent_root = tmp_path / "artifacts"
    db_path = tmp_path / "data" / "assistant.db"
    generated = persistent_root / "report.txt"
    generated.parent.mkdir(parents=True)
    generated.write_text("durable", encoding="utf-8")
    store = ArtifactStore(root, persistent_root=persistent_root, db_path=db_path)
    ref = store.register_generated(
        "session-a",
        generated,
        retention="persistent",
    )

    store.cleanup_session("session-a")
    reopened = ArtifactStore(root, persistent_root=persistent_root, db_path=db_path)
    reopened_ref = reopened.public_ref("session-a", ref["artifact_id"])

    assert reopened_ref["name"] == "report.txt"
    assert generated.read_text(encoding="utf-8") == "durable"


def test_user_screenshot_schema_has_no_parameters(tmp_path):
    store = ArtifactStore(tmp_path / "attachments")
    tool = ScreenshotTool(store, tmp_path / "attachments" / "screenshots")
    assert tool.definition()["inputSchema"]["properties"] == {}
