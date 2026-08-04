from __future__ import annotations

import pytest

from pc_assistant.attachments import AttachmentStore


DATA_URL = "data:image/jpeg;base64,AAAA"


def test_reference_is_small_and_hydration_is_request_only(tmp_path):
    store = AttachmentStore(tmp_path / "attachments")
    ref = store.put_data_url("session-a", DATA_URL, caption="sample")

    assert ref["type"] == "image_ref"
    assert "base64" not in str(ref)
    messages = [{"role": "user", "content": [ref]}]
    hydrated = store.hydrate_messages("session-a", messages)

    assert hydrated[0]["content"][0]["type"] == "image"
    assert hydrated[0]["content"][0]["image_url"] == DATA_URL
    assert messages[0]["content"][0]["type"] == "image_ref"


def test_historical_images_are_not_rehydrated_without_explicit_reference(tmp_path):
    store = AttachmentStore(tmp_path / "attachments")
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
    store = AttachmentStore(tmp_path / "attachments")
    ref = store.put_data_url("session-a", DATA_URL)

    with pytest.raises(KeyError):
        store.hydrate_ref("session-b", ref)

    with pytest.raises(KeyError):
        store.metadata("session-b", ref["attachment_id"])


def test_text_model_manifest_contains_id_without_base64(tmp_path):
    store = AttachmentStore(tmp_path / "attachments")
    ref = store.put_data_url("session-a", DATA_URL, caption="error screenshot")

    manifested = store.manifest_messages(
        "session-a",
        [{"role": "user", "content": [{"type": "text", "text": "look"}, ref]}],
    )

    rendered = str(manifested)
    assert ref["attachment_id"] in rendered
    assert "available image" in rendered
    assert "error screenshot" in rendered
    assert "base64" not in rendered


def test_expired_attachment_is_deleted(tmp_path):
    now = [100.0]
    store = AttachmentStore(
        tmp_path / "attachments",
        ttl_seconds=10,
        clock=lambda: now[0],
    )
    ref = store.put_data_url("session-a", DATA_URL)
    files = list((tmp_path / "attachments").rglob("*.jpg"))
    assert len(files) == 1

    now[0] = 111.0
    with pytest.raises(KeyError):
        store.hydrate_ref("session-a", ref)
    assert not files[0].exists()


def test_rejects_non_image_or_invalid_base64(tmp_path):
    store = AttachmentStore(tmp_path / "attachments")
    with pytest.raises(ValueError):
        store.put_data_url("s", "data:text/plain;base64,AAAA")
    with pytest.raises(ValueError):
        store.put_data_url("s", "data:image/jpeg;base64,***")


def test_startup_sweep_removes_stale_files(tmp_path):
    import os

    stale = tmp_path / "attachments" / "old-session" / "old.jpg"
    stale.parent.mkdir(parents=True)
    stale.write_bytes(b"old")
    os.utime(stale, (1, 1))

    AttachmentStore(tmp_path / "attachments", ttl_seconds=10, clock=lambda: 100.0)
    assert not stale.exists()


def test_register_managed_path_does_not_copy_image(tmp_path):
    from PIL import Image

    root = tmp_path / "attachments"
    image = root / "screenshots" / "capture.png"
    image.parent.mkdir(parents=True)
    Image.new("RGB", (16, 12), "blue").save(image)
    store = AttachmentStore(root)

    ref = store.register_path("session-a", image, source="tool:screen")

    assert ref["media_type"] == "image/png"
    assert ref["width"] == 16
    assert ref["height"] == 12
    assert [path for path in root.rglob("*") if path.is_file()] == [image]
