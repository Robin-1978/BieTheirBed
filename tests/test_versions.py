from __future__ import annotations

import importlib.util
import json
from pathlib import Path

from knoa_platform import __version__


def _manager():
    path = Path(__file__).parents[1] / "scripts/version_manager.py"
    spec = importlib.util.spec_from_file_location("version_manager", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _mobile_tree(root: Path, version: str = "1.2.3", code: int = 7) -> None:
    mobile = root / "apps/knoa-mobile"
    mobile.mkdir(parents=True)
    (mobile / "app.json").write_text(
        json.dumps(
            {"expo": {"version": version, "android": {"versionCode": code}}},
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    (mobile / "package.json").write_text(
        json.dumps({"name": "knoa-mobile", "version": version}, indent=2)
        + "\n",
        encoding="utf-8",
    )
    (mobile / "package-lock.json").write_text(
        json.dumps(
            {
                "name": "knoa-mobile",
                "version": version,
                "packages": {"": {"name": "knoa-mobile", "version": version}},
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def _release_manifest(root: Path, *, platform: str, mobile: str, code: int) -> None:
    release = root / "release"
    release.mkdir(parents=True, exist_ok=True)
    (release / "versions.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "platform_version": platform,
                "mobile_version": mobile,
                "mobile_android_version_code": code,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def test_repository_product_versions_are_independent_and_consistent() -> None:
    manager = _manager()
    root = Path(__file__).parents[1]

    platform, mobile, code = manager.check(root)

    assert platform == __version__
    manifest = manager.release_manifest(root)
    assert mobile == manifest["mobile_version"]
    assert code == manifest["mobile_android_version_code"]
    assert platform != mobile


def test_platform_bump_changes_only_platform_version(tmp_path: Path) -> None:
    manager = _manager()
    source = tmp_path / "src/knoa_platform"
    source.mkdir(parents=True)
    (source / "__init__.py").write_text(
        '__version__ = "1.2.3"\n',
        encoding="utf-8",
    )
    _mobile_tree(tmp_path)
    _release_manifest(tmp_path, platform="1.2.3", mobile="1.2.3", code=7)

    assert manager.bump_platform(tmp_path, "minor") == ("1.2.3", "1.3.0")
    assert manager.mobile_version(tmp_path) == ("1.2.3", 7)
    assert manager.release_manifest(tmp_path)["platform_version"] == "1.3.0"


def test_mobile_bump_updates_all_mobile_versions_and_build_code(tmp_path: Path) -> None:
    manager = _manager()
    source = tmp_path / "src/knoa_platform"
    source.mkdir(parents=True)
    (source / "__init__.py").write_text(
        '__version__ = "9.0.0"\n',
        encoding="utf-8",
    )
    _mobile_tree(tmp_path)
    _release_manifest(tmp_path, platform="9.0.0", mobile="1.2.3", code=7)

    assert manager.bump_mobile(tmp_path, "patch") == (
        "1.2.3",
        "1.2.4",
        7,
        8,
    )
    assert manager.mobile_version(tmp_path) == ("1.2.4", 8)
    assert manager.platform_version(tmp_path) == "9.0.0"
    assert manager.release_manifest(tmp_path)["mobile_android_version_code"] == 8


def test_check_rejects_stale_release_manifest(tmp_path: Path) -> None:
    manager = _manager()
    source = tmp_path / "src/knoa_platform"
    source.mkdir(parents=True)
    (source / "__init__.py").write_text('__version__ = "1.2.3"\n', encoding="utf-8")
    _mobile_tree(tmp_path)
    _release_manifest(tmp_path, platform="1.2.2", mobile="1.2.3", code=7)

    try:
        manager.check(tmp_path)
    except ValueError as exc:
        assert "does not match" in str(exc)
    else:
        raise AssertionError("stale release manifest should fail the version check")
