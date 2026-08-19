from __future__ import annotations

from pathlib import Path

import pytest

from knoa_platform.release.health import probe
from scripts.materialize_release_payload import materialize_payload


def _inputs(root: Path, target_os: str) -> tuple[Path, Path, Path | None]:
    runtime = root / "runtime-source"
    python = runtime / ("python.exe" if target_os == "windows" else "bin/python3")
    python.parent.mkdir(parents=True)
    python.write_bytes(b"runtime")
    if target_os == "linux":
        python.chmod(0o755)
    application = root / "application-source"
    package = application / "knoa_platform"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("__version__ = 'test'\n", encoding="utf-8")
    winsw = None
    if target_os == "windows":
        winsw = root / "WinSW.exe"
        winsw.write_bytes(b"winsw")
    return runtime, application, winsw


@pytest.mark.parametrize("target_os", ["windows", "linux"])
@pytest.mark.parametrize(
    ("role", "expected"),
    [
        ("hub", {"knoa-health", "knoa-hub"}),
        ("node", {"knoa-health", "knoa-node"}),
        ("all", {"knoa-health", "knoa-hub", "knoa-node"}),
    ],
)
def test_materialized_payload_has_role_specific_launchers(
    tmp_path: Path,
    target_os: str,
    role: str,
    expected: set[str],
) -> None:
    runtime, application, winsw = _inputs(tmp_path, target_os)
    output = tmp_path / "output"

    result = materialize_payload(
        role=role,
        target_os=target_os,
        runtime_source=runtime,
        application_source=application,
        output=output,
        winsw_source=winsw,
    )

    suffix = ".cmd" if target_os == "windows" else ""
    assert set(result["launchers"]) == {f"{name}{suffix}" for name in expected}
    health = (output / "bin" / f"knoa-health{suffix}").read_text(encoding="utf-8")
    assert "knoa_platform.release.health" in health
    assert role in health
    assert (output / "install").is_dir()
    if target_os == "windows":
        assert (output / "service" / "WinSW.exe").read_bytes() == b"winsw"
    if role in {"node", "all"}:
        node = (output / "bin" / f"knoa-node{suffix}").read_text(encoding="utf-8")
        assert "knoa_platform.service" in node


def test_release_health_probe_covers_role_boundaries() -> None:
    hub = probe("hub")
    node = probe("node")
    assert hub["checks"] == ["hub_import"]
    assert "gateway_openapi" in node["checks"]


def test_windows_payload_requires_signed_winsw(tmp_path: Path) -> None:
    runtime, application, _winsw = _inputs(tmp_path, "windows")
    with pytest.raises(ValueError, match="WinSW"):
        materialize_payload(
            role="node",
            target_os="windows",
            runtime_source=runtime,
            application_source=application,
            output=tmp_path / "output",
        )


def test_service_assets_resolve_the_atomic_release_pointer(tmp_path: Path) -> None:
    runtime, application, _winsw = _inputs(tmp_path, "linux")
    output = tmp_path / "output"
    materialize_payload(
        role="all",
        target_os="linux",
        runtime_source=runtime,
        application_source=application,
        output=output,
    )

    hub_unit = (output / "install" / "knoa-hub.service").read_text(encoding="utf-8")
    node_unit = (output / "install" / "knoa-node.service").read_text(encoding="utf-8")
    assert "knoa-update run" in hub_unit
    assert "knoa-update run" in node_unit
    assert "/versions/" not in hub_unit
    assert "/versions/" not in node_unit
