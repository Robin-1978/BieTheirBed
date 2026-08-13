#!/usr/bin/env python3
"""Check and bump independent Knoa Platform and Mobile product versions."""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

_SEMVER = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")


def _repo_root(value: str | None = None) -> Path:
    return (
        Path(value).expanduser().resolve()
        if value
        else Path(__file__).resolve().parent.parent
    )


def _semver(value: str) -> tuple[int, int, int]:
    match = _SEMVER.fullmatch(value.strip())
    if match is None:
        raise ValueError(f"Invalid semantic version: {value}")
    return tuple(int(part) for part in match.groups())


def _next_version(current: str, requested: str) -> str:
    major, minor, patch = _semver(current)
    if requested == "major":
        return f"{major + 1}.0.0"
    if requested == "minor":
        return f"{major}.{minor + 1}.0"
    if requested == "patch":
        return f"{major}.{minor}.{patch + 1}"
    target = _semver(requested)
    if target <= (major, minor, patch):
        raise ValueError("New version must be greater than the current version")
    return requested


def _json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def platform_version(root: Path) -> str:
    path = root / "src/knoa_platform/__init__.py"
    match = re.search(
        r'^__version__ = "([^"]+)"$',
        path.read_text(encoding="utf-8"),
        re.MULTILINE,
    )
    if match is None:
        raise ValueError("Platform version source is missing")
    version = match.group(1)
    _semver(version)
    return version


def mobile_version(root: Path) -> tuple[str, int]:
    app = _json(root / "apps/knoa-mobile/app.json")["expo"]
    package = _json(root / "apps/knoa-mobile/package.json")
    lock = _json(root / "apps/knoa-mobile/package-lock.json")
    versions = {
        str(app["version"]),
        str(package["version"]),
        str(lock["version"]),
        str(lock["packages"][""]["version"]),
    }
    if len(versions) != 1:
        raise ValueError(
            "Mobile version mismatch across app.json, package.json, and package-lock.json"
        )
    version = versions.pop()
    _semver(version)
    version_code = app["android"]["versionCode"]
    if not isinstance(version_code, int) or version_code < 1:
        raise ValueError("Mobile Android versionCode must be a positive integer")
    return version, version_code


def check(root: Path) -> tuple[str, str, int]:
    platform = platform_version(root)
    mobile, version_code = mobile_version(root)
    return platform, mobile, version_code


def _replace_once(path: Path, pattern: str, replacement: str) -> None:
    original = path.read_text(encoding="utf-8")
    updated, count = re.subn(
        pattern,
        replacement,
        original,
        count=1,
        flags=re.MULTILINE,
    )
    if count != 1:
        raise ValueError(f"Could not update version in {path}")
    path.write_text(updated, encoding="utf-8")


def bump_platform(root: Path, requested: str) -> tuple[str, str]:
    current = platform_version(root)
    target = _next_version(current, requested)
    _replace_once(
        root / "src/knoa_platform/__init__.py",
        rf'^__version__ = "{re.escape(current)}"$',
        f'__version__ = "{target}"',
    )
    return current, target


def bump_mobile(root: Path, requested: str) -> tuple[str, str, int, int]:
    current, current_code = mobile_version(root)
    target = _next_version(current, requested)
    app_path = root / "apps/knoa-mobile/app.json"
    package_path = root / "apps/knoa-mobile/package.json"
    lock_path = root / "apps/knoa-mobile/package-lock.json"
    _replace_once(
        app_path,
        rf'^(\s+"version": )"{re.escape(current)}"(,?)$',
        rf'\1"{target}"\2',
    )
    _replace_once(
        app_path,
        rf'^(\s+"versionCode": ){current_code}(,?)$',
        rf'\g<1>{current_code + 1}\2',
    )
    _replace_once(
        package_path,
        rf'^(\s+"version": )"{re.escape(current)}"(,?)$',
        rf'\1"{target}"\2',
    )
    _replace_once(
        lock_path,
        rf'^(\s+"version": )"{re.escape(current)}"(,?)$',
        rf'\1"{target}"\2',
    )
    _replace_once(
        lock_path,
        rf'^(\s+"version": )"{re.escape(current)}"(,?)$',
        rf'\1"{target}"\2',
    )
    mobile_version(root)
    return current, target, current_code, current_code + 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=None, help=argparse.SUPPRESS)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("check", help="Validate and show both product versions")
    bump = commands.add_parser("bump", help="Bump one independent product version")
    bump.add_argument("product", choices=("platform", "mobile"))
    bump.add_argument("version", help="major, minor, patch, or an explicit X.Y.Z")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = _repo_root(args.root)
    try:
        if args.command == "check":
            platform, mobile, code = check(root)
            print(f"platform={platform}")
            print(f"mobile={mobile}")
            print(f"mobile_version_code={code}")
            return 0
        if args.product == "platform":
            old, new = bump_platform(root, args.version)
            print(f"Knoa Platform: {old} -> {new}")
        else:
            old, new, old_code, new_code = bump_mobile(root, args.version)
            print(f"Knoa Mobile: {old} -> {new}")
            print(f"Android versionCode: {old_code} -> {new_code}")
    except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
