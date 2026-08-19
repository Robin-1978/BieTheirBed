from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _copy_tree(source: Path, destination: Path) -> None:
    if not source.is_dir():
        raise ValueError(f"Release input directory is missing: {source}")
    for candidate in source.rglob("*"):
        if candidate.is_symlink():
            raise ValueError("Release inputs cannot contain symlinks")
    shutil.copytree(source, destination, symlinks=False)


def _runtime_python(target_os: str, runtime_root: Path) -> str:
    candidates = (
        ("python.exe",)
        if target_os == "windows"
        else ("bin/python3", "bin/python")
    )
    for relative in candidates:
        if (runtime_root / relative).is_file():
            return relative
    raise ValueError("Embedded Python Runtime executable is missing")


def _python_bootstrap(module: str) -> str:
    return (
        "import runpy,sys;"
        "app=sys.argv.pop(1);"
        "sys.path.insert(0,app);"
        f"sys.argv[0]={module!r};"
        f"runpy.run_module({module!r},run_name='__main__',alter_sys=True)"
    )


def _linux_launcher(python_path: str, module: str, fixed_args: tuple[str, ...]) -> str:
    quoted_args = " ".join(f"'{value}'" for value in fixed_args)
    suffix = f" {quoted_args}" if quoted_args else ""
    bootstrap = _python_bootstrap(module).replace('"', '\\"')
    return f"""#!/bin/sh
set -eu
RELEASE_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
exec "$RELEASE_ROOT/runtime/{python_path}" -I -c "{bootstrap}" \
  "$RELEASE_ROOT/app"{suffix} "$@"
"""


def _windows_launcher(module: str, fixed_args: tuple[str, ...]) -> str:
    joined_args = " ".join(f'"{value}"' for value in fixed_args)
    suffix = f" {joined_args}" if joined_args else ""
    bootstrap = _python_bootstrap(module).replace('"', '\\"')
    return (
        "@echo off\r\n"
        "setlocal\r\n"
        "set \"RELEASE_ROOT=%~dp0..\"\r\n"
        f'"%RELEASE_ROOT%\\runtime\\python.exe" -I -c "{bootstrap}" '
        f'"%RELEASE_ROOT%\\app"{suffix} %*\r\n'
    )


def _write_launcher(
    bin_root: Path,
    *,
    target_os: str,
    name: str,
    python_path: str,
    module: str,
    fixed_args: tuple[str, ...] = (),
) -> Path:
    suffix = ".cmd" if target_os == "windows" else ""
    path = bin_root / f"{name}{suffix}"
    content = (
        _windows_launcher(module, fixed_args)
        if target_os == "windows"
        else _linux_launcher(python_path, module, fixed_args)
    )
    path.write_text(content, encoding="utf-8", newline="")
    if target_os == "linux":
        path.chmod(0o755)
    return path


def materialize_payload(
    *,
    role: str,
    target_os: str,
    runtime_source: Path,
    application_source: Path,
    output: Path,
    winsw_source: Path | None = None,
) -> dict[str, object]:
    if role != "all":
        raise ValueError("Product Release must be the universal all-role Host Bundle")
    if target_os not in {"windows", "linux"}:
        raise ValueError("Release target OS must be windows or linux")
    if output.exists() and any(output.iterdir()):
        raise ValueError("Release payload output must be empty")
    output.mkdir(parents=True, exist_ok=True)
    runtime_root = output / "runtime"
    application_root = output / "app"
    _copy_tree(runtime_source, runtime_root)
    _copy_tree(application_source, application_root)
    _copy_tree(ROOT / "deploy" / "product" / target_os, output / "install")
    if target_os == "windows":
        if winsw_source is None or not winsw_source.is_file():
            raise ValueError("Windows product Release requires a WinSW executable")
        service_root = output / "service"
        service_root.mkdir()
        shutil.copy2(winsw_source, service_root / "WinSW.exe")
    if not (application_root / "knoa_platform" / "__init__.py").is_file():
        raise ValueError("Materialized application must contain knoa_platform")
    python_path = _runtime_python(target_os, runtime_root)
    bin_root = output / "bin"
    bin_root.mkdir()
    launchers = [
        _write_launcher(
            bin_root,
            target_os=target_os,
            name="knoa-health",
            python_path=python_path,
            module="knoa_platform.release.health",
            fixed_args=("--role", role),
        )
    ]
    launchers.append(
        _write_launcher(
            bin_root,
            target_os=target_os,
            name="knoa-hub",
            python_path=python_path,
            module="knoa_platform.hub",
        )
    )
    launchers.append(
        _write_launcher(
            bin_root,
            target_os=target_os,
            name="knoa-node",
            python_path=python_path,
            module="knoa_platform.service",
        )
    )
    launchers.append(
        _write_launcher(
            bin_root,
            target_os=target_os,
            name="knoa-host-lifecycle",
            python_path=python_path,
            module="knoa_platform.host_lifecycle",
        )
    )
    return {
        "role": role,
        "target_os": target_os,
        "runtime_python": python_path,
        "launchers": [path.name for path in launchers],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--role", choices=("all",), default="all")
    parser.add_argument("--target-os", choices=("windows", "linux"), required=True)
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--application", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--winsw", type=Path)
    args = parser.parse_args()
    result = materialize_payload(
        role=args.role,
        target_os=args.target_os,
        runtime_source=args.runtime,
        application_source=args.application,
        output=args.output,
        winsw_source=args.winsw,
    )
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
