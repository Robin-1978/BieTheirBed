from pathlib import Path

import pytest
import yaml

from knoa_platform.extensions.capability_bundle import load_capability_bundle


def test_capability_manifest_rejects_arbitrary_preflight_and_unsafe_paths(
    tmp_path: Path,
) -> None:
    source = tmp_path / "unsafe"
    source.mkdir()
    manifest = {
        "schema_version": 1,
        "id": "unsafe",
        "version": "1.0.0",
        "display_name": "Unsafe",
        "description": "Unsafe package",
        "components": {"mcp": ["../escape"]},
        "health_checks": [{"kind": "shell_script", "value": "curl | sh"}],
    }
    (source / "capability.yaml").write_text(
        yaml.safe_dump(manifest), encoding="utf-8",
    )
    with pytest.raises(ValueError):
        load_capability_bundle(source)


def test_capability_package_store_freezes_bundle_bytes(tmp_path: Path) -> None:
    from knoa_platform.extensions.package_store import PackageStore

    source = tmp_path / "sample"
    source.mkdir()
    (source / "capability.yaml").write_text("schema_version: 1\n", encoding="utf-8")
    package = PackageStore(tmp_path / "packages").import_directory(
        "capability", source, imported_by="owner",
    )
    assert package.package_id.startswith("capability-")
    assert package.kind == "capability"
