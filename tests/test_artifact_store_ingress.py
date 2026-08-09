from __future__ import annotations

import stat

import pytest

from pc_assistant.artifacts import ArtifactStore


DATA_URL = (
    "data:image/png;base64,"
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGP4z8AAAAMBAQDJ/pLvAAAAAElFTkSuQmCC"
)


def test_reference_is_small_and_hydration_is_request_only(tmp_path):
    store = ArtifactStore(tmp_path / "attachments")
    ref = store.put_data_url("session-a", DATA_URL, caption="sample")

    assert ref["type"] == "image_ref"
    assert "base64" not in str(ref)
    path = next((tmp_path / "attachments").rglob("*.png"))
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    messages = [{"role": "user", "content": [ref]}]
    hydrated = store.hydrate_messages("session-a", messages)

    assert hydrated[0]["content"][0]["type"] == "image"
    assert hydrated[0]["content"][0]["image_url"] == DATA_URL
    assert messages[0]["content"][0]["type"] == "image_ref"


def test_historical_images_are_not_rehydrated_without_explicit_reference(tmp_path):
    store = ArtifactStore(tmp_path / "attachments")
    ref = store.put_data_url("session-a", DATA_URL)
    messages = [
        {"role": "user", "content": [{"type": "text", "text": "first"}, ref]},
        {"role": "assistant", "content": "seen"},
        {"role": "user", "content": "new unrelated question"},
    ]

    hydrated = store.hydrate_messages("session-a", messages)

    assert "data:image" not in str(hydrated)
    assert "historical image reference" in str(hydrated)


def test_reference_is_session_scoped(tmp_path):
    store = ArtifactStore(tmp_path / "attachments")
    ref = store.put_data_url("session-a", DATA_URL)

    with pytest.raises(KeyError):
        store.hydrate_ref("session-b", ref)

    with pytest.raises(KeyError):
        store.metadata("session-b", ref["artifact_id"])


def test_text_model_manifest_contains_id_without_base64(tmp_path):
    store = ArtifactStore(tmp_path / "attachments")
    ref = store.put_data_url("session-a", DATA_URL, caption="error screenshot")

    manifested = store.manifest_messages(
        "session-a",
        [{"role": "user", "content": [{"type": "text", "text": "look"}, ref]}],
    )

    rendered = str(manifested)
    assert ref["artifact_id"] in rendered
    assert "available image" in rendered
    assert "error screenshot" in rendered
    assert "base64" not in rendered


def test_expired_attachment_is_deleted(tmp_path):
    now = [100.0]
    store = ArtifactStore(
        tmp_path / "attachments",
        ttl_seconds=10,
        clock=lambda: now[0],
    )
    ref = store.put_data_url("session-a", DATA_URL)
    files = list((tmp_path / "attachments").rglob("*.png"))
    assert len(files) == 1

    now[0] = 111.0
    with pytest.raises(KeyError):
        store.hydrate_ref("session-a", ref)
    assert not files[0].exists()


def test_accepts_named_file_and_rejects_invalid_base64(tmp_path):
    store = ArtifactStore(tmp_path / "attachments")
    ref = store.put_data_url(
        "s",
        "data:text/plain;base64,5bCP6K+6",
        name="notes.txt",
    )

    assert ref["type"] == "file_ref"
    assert ref["kind"] == "file"
    assert ref["name"] == "notes.txt"
    assert store.read_text("s", ref["artifact_id"])["content"] == "小诺"
    hydrated = store.hydrate_messages(
        "s",
        [{"role": "user", "content": [ref]}],
    )
    assert "available file" in hydrated[0]["content"][0]["text"]
    assert ref["artifact_id"] in hydrated[0]["content"][0]["text"]
    assert "read_artifact" in hydrated[0]["content"][0]["text"]
    with pytest.raises(ValueError):
        store.put_data_url("s", "data:image/jpeg;base64,***")


def test_startup_never_deletes_unregistered_files(tmp_path):
    import os

    stale = tmp_path / "attachments" / "old-session" / "old.jpg"
    stale.parent.mkdir(parents=True)
    stale.write_bytes(b"old")
    os.utime(stale, (1, 1))

    ArtifactStore(tmp_path / "attachments", ttl_seconds=10, clock=lambda: 100.0)
    assert stale.read_bytes() == b"old"


def test_register_generated_path_does_not_copy_image(tmp_path):
    from PIL import Image

    root = tmp_path / "attachments"
    image = root / "screenshots" / "capture.png"
    image.parent.mkdir(parents=True)
    Image.new("RGB", (16, 12), "blue").save(image)
    store = ArtifactStore(root)

    ref = store.register_path("session-a", image, source="tool:screen")

    assert ref["media_type"] == "image/png"
    assert ref["width"] == 16
    assert ref["height"] == 12
    assert [path for path in root.rglob("*") if path.is_file()] == [image]


def test_hydration_rejects_registered_image_that_changed_size(tmp_path):
    store = ArtifactStore(tmp_path / "attachments", max_bytes=1_024)
    ref = store.put_data_url("session-a", DATA_URL)
    path = next((tmp_path / "attachments").rglob("*.png"))
    with path.open("ab") as stream:
        stream.write(b"changed")

    with pytest.raises(OSError, match="size changed before hydration"):
        store.hydrate_ref("session-a", ref)
