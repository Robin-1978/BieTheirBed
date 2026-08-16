"""Local-only publication commands for the personal Android update channel."""

from __future__ import annotations

import sys
from pathlib import Path

from knoa_platform.config import load_config
from knoa_platform.mobile_releases import AndroidReleaseRepository, read_apk_version
from knoa_platform.runtime import RuntimePaths


def run_android_release_admin(
    config_path: str | None,
    *,
    action: str,
    apk_path: str = "",
    version_name: str = "",
    version_code: int = 0,
    min_version_code: int = 1,
    notes: str = "",
) -> int:
    """Publish or inspect releases without exposing an administrative HTTP API."""
    config = load_config(config_path) if config_path else load_config()
    repository = AndroidReleaseRepository(
        RuntimePaths.from_root(config.runtime_root).data / "mobile-releases" / "android"
    )
    try:
        if action == "publish":
            manifest_version_name, manifest_version_code = read_apk_version(
                Path(apk_path)
            )
            if version_name and version_name != manifest_version_name:
                raise ValueError("Android version name override does not match the APK")
            if version_code and version_code != manifest_version_code:
                raise ValueError("Android version code override does not match the APK")
            release = repository.publish(
                Path(apk_path),
                version_name=version_name or manifest_version_name,
                version_code=version_code or manifest_version_code,
                min_supported_version_code=min_version_code,
                release_notes=notes,
            )
            print(f"version_name={release.version_name}")
            print(f"version_code={release.version_code}")
            print(f"size_bytes={release.size_bytes}")
            print(f"sha256={release.sha256}")
            print(f"package={repository.package_path(release)}")
            return 0
        if action == "latest":
            release = repository.latest()
            assert release is not None
            print(f"version_name={release.version_name}")
            print(f"version_code={release.version_code}")
            print(f"min_supported_version_code={release.min_supported_version_code}")
            print(f"size_bytes={release.size_bytes}")
            print(f"sha256={release.sha256}")
            print(f"published_at={release.published_at}")
            print(f"package={repository.package_path(release)}")
            return 0
    except (LookupError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    raise ValueError(f"Unknown Android release administration action: {action}")
