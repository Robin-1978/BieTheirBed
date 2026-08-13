from __future__ import annotations

import stat

from knoa_platform.tools.artifacts import ArtifactPaths


def test_generated_screenshot_path_is_unique_and_below_root(tmp_path):
    paths = ArtifactPaths(tmp_path / "attachments" / "screenshots")

    first = paths.allocate(prefix="screen-look", suffix=".jpg")
    second = paths.allocate(prefix="screen-look", suffix=".jpg")

    assert first.parent == paths.root
    assert second.parent == paths.root
    assert first != second
    assert first.name.startswith("screen-look-")
    assert first.suffix == ".jpg"
    assert stat.S_IMODE(paths.root.stat().st_mode) == 0o700
