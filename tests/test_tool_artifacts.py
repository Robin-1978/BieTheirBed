from __future__ import annotations

import pytest

from pc_assistant.tools.artifacts import ArtifactPaths


def test_generated_screenshot_path_is_unique_and_below_root(tmp_path):
    paths = ArtifactPaths(tmp_path / "attachments" / "screenshots")

    first = paths.allocate(prefix="screen-look", suffix=".jpg")
    second = paths.allocate(prefix="screen-look", suffix=".jpg")

    assert first.parent == paths.root
    assert second.parent == paths.root
    assert first != second
    assert first.name.startswith("screen-look-")
    assert first.suffix == ".jpg"


def test_relative_requested_path_stays_below_artifact_root(tmp_path):
    paths = ArtifactPaths(tmp_path / "screenshots")

    result = paths.allocate(prefix="screen-look", suffix=".jpg", requested="named/capture.jpg")

    assert result == paths.root / "named" / "capture.jpg"


def test_requested_path_cannot_escape_artifact_root(tmp_path):
    paths = ArtifactPaths(tmp_path / "screenshots")

    with pytest.raises(ValueError, match="must stay below"):
        paths.allocate(prefix="screen-look", suffix=".jpg", requested="../outside.jpg")
