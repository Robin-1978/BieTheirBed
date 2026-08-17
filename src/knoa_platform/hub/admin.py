"""Local administration for Hosted Hub identity, enrollment and storage."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import shutil
import sqlite3
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from knoa_platform.gateway.protocol import NodeHubEnrollmentRequest
from knoa_platform.mobile_releases import (
    AndroidRelease,
    AndroidReleaseRepository,
    read_apk_version,
)
from knoa_platform.node_hub import NodeHubService, NodeHubStore
from knoa_platform.node_identity import NodeIdentityStore
from knoa_platform.runtime import RuntimePaths

_BACKUP_VERSION = "knoa-hosted-backup-v2"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="knoa-hub-admin")
    parser.add_argument(
        "--endpoint",
        default=os.environ.get("KNOA_HUB_ADMIN_ENDPOINT", "http://127.0.0.1:9529"),
        help="Loopback Hosted Hub endpoint used for administration",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    grant = commands.add_parser(
        "account-grant",
        help="Create one short-lived account enrollment payload and QR code",
    )
    _public_hub_url(grant)
    grant.add_argument("--ttl", type=int, default=900)

    reset = commands.add_parser(
        "password-reset-grant",
        help="Create one short-lived password recovery payload and QR code",
    )
    _public_hub_url(reset)
    reset.add_argument("--login", required=True)
    reset.add_argument("--ttl", type=int, default=900)

    enroll = commands.add_parser(
        "node-enroll",
        help="Enroll this local Node into one Hosted Workspace",
    )
    _public_hub_url(enroll)
    enroll.add_argument("--workspace-id", required=True)
    enroll.add_argument("--runtime-root", default=os.environ.get("KNOA_RUNTIME_ROOT", ""))
    enroll.add_argument("--display-name", default="Knoa Node")

    backup = commands.add_parser(
        "backup",
        help="Create a transactionally consistent Hosted control and Workspace backup",
    )
    backup.add_argument("--root", required=True)
    backup.add_argument("--output", required=True)

    restore = commands.add_parser(
        "restore",
        help="Restore a Hosted backup into a new empty root",
    )
    restore.add_argument("--backup", required=True)
    restore.add_argument("--root", required=True)

    mobile_publish = commands.add_parser(
        "mobile-publish",
        help="Publish a signed Knoa Android APK as a Hosted platform release",
    )
    mobile_publish.add_argument("apk")
    mobile_publish.add_argument("--root", required=True)
    mobile_publish.add_argument("--min-version-code", type=int, default=1)
    mobile_publish.add_argument("--notes", default="")

    mobile_latest = commands.add_parser(
        "mobile-latest",
        help="Inspect the latest Hosted Android platform release",
    )
    mobile_latest.add_argument("--root", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "backup":
        return _backup(Path(args.root), Path(args.output))
    if args.command == "restore":
        return _restore(Path(args.backup), Path(args.root))
    if args.command == "mobile-publish":
        return _mobile_publish(
            Path(args.root),
            Path(args.apk),
            min_version_code=args.min_version_code,
            notes=args.notes,
        )
    if args.command == "mobile-latest":
        return _mobile_latest(Path(args.root))
    if args.command == "node-enroll":
        account_token = os.environ.get("KNOA_HUB_ACCOUNT_TOKEN", "")
        if len(account_token) < 32:
            parser.error("KNOA_HUB_ACCOUNT_TOKEN must contain at least 32 characters")
        return asyncio.run(
            _node_enroll(
                hub_url=args.hub_url,
                workspace_id=args.workspace_id,
                runtime_root=args.runtime_root,
                display_name=args.display_name,
                account_token=account_token,
            )
        )
    bootstrap_token = os.environ.get("KNOA_HUB_BOOTSTRAP_TOKEN", "")
    if len(bootstrap_token) < 32:
        parser.error("KNOA_HUB_BOOTSTRAP_TOKEN must contain at least 32 characters")
    if args.command == "account-grant":
        return _grant_payload(
            endpoint=args.endpoint,
            hub_url=args.hub_url,
            bootstrap_token=bootstrap_token,
            ttl_seconds=args.ttl,
            path="/v1/hosted/account-enrollment-grants",
            request={"ttl_seconds": args.ttl},
            version="knoa-hosted-account-v1",
            secret_key="grant_secret",
        )
    if args.command == "password-reset-grant":
        return _grant_payload(
            endpoint=args.endpoint,
            hub_url=args.hub_url,
            bootstrap_token=bootstrap_token,
            ttl_seconds=args.ttl,
            path="/v1/hosted/password-reset-grants",
            request={"login_identity": args.login, "ttl_seconds": args.ttl},
            version="knoa-hosted-password-reset-v1",
            secret_key="grant_secret",
        )
    raise ValueError(f"Unknown Hosted Hub administration command: {args.command}")


def _public_hub_url(parser: argparse.ArgumentParser) -> None:
    configured = os.environ.get("KNOA_HUB_PUBLIC_URL", "")
    parser.add_argument("--hub-url", default=configured, required=not bool(configured))


def _grant_payload(
    *,
    endpoint: str,
    hub_url: str,
    bootstrap_token: str,
    ttl_seconds: int,
    path: str,
    request: dict[str, object],
    version: str,
    secret_key: str,
) -> int:
    try:
        normalized_endpoint = _url(endpoint, allow_http_loopback=True)
        normalized_hub_url = _url(hub_url, allow_http_loopback=False)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if not 60 <= ttl_seconds <= 3600:
        print("TTL must be between 60 and 3600 seconds", file=sys.stderr)
        return 2
    try:
        response = httpx.post(
            f"{normalized_endpoint}{path}",
            headers={"Authorization": f"Bearer {bootstrap_token}"},
            json=request,
            timeout=15.0,
        )
        response.raise_for_status()
        grant = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        print(f"Could not create Hosted grant: {exc}", file=sys.stderr)
        return 1
    payload = json.dumps(
        {
            "version": version,
            "hub_url": normalized_hub_url,
            "grant_id": str(grant["grant_id"]),
            secret_key: str(grant["secret"]),
            "expires_at": float(grant["expires_at"]),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    print(f"hosted_setup_json={payload}")
    _print_qr(payload)
    return 0


async def _node_enroll(
    *,
    hub_url: str,
    workspace_id: str,
    runtime_root: str,
    display_name: str,
    account_token: str,
) -> int:
    normalized_hub_url = _url(hub_url, allow_http_loopback=False)
    if not re.fullmatch(r"ws_[A-Za-z0-9_-]{12,96}", workspace_id):
        print("Workspace ID is invalid", file=sys.stderr)
        return 2
    workspace_url = f"{normalized_hub_url}/workspaces/{workspace_id}"
    headers = {"Authorization": f"Bearer {account_token}"}
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            hub_response = await client.get(f"{workspace_url}/v1/hub", headers=headers)
            hub_response.raise_for_status()
            hub = hub_response.json()
            grant_response = await client.post(
                f"{workspace_url}/v1/node-enrollment-grants",
                headers=headers,
                json={"ttl_seconds": 600},
            )
            grant_response.raise_for_status()
            grant = grant_response.json()
        paths = RuntimePaths.from_root(runtime_root or None)
        identity = NodeIdentityStore(paths.data / "node-identity.json").load_or_create()
        service = NodeHubService(NodeHubStore(paths.data / "node-hub.json"), identity)
        enrollment = await service.enroll(
            NodeHubEnrollmentRequest(
                hub_url=workspace_url,
                hub_id=str(hub["hub_id"]),
                hub_signing_public_key=str(hub["signing_public_key"]),
                grant_id=str(grant["grant_id"]),
                grant_secret=str(grant["secret"]),
                challenge=str(grant["challenge"]),
                display_name=display_name,
            )
        )
    except (httpx.HTTPError, KeyError, PermissionError, ValueError) as exc:
        print(f"Could not enroll local Node: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(enrollment.__dict__, sort_keys=True))
    print("Restart the Knoa Node service to start its outbound Relay connection.")
    return 0


def _mobile_publish(
    root: Path,
    apk: Path,
    *,
    min_version_code: int,
    notes: str,
) -> int:
    repository = AndroidReleaseRepository(
        root.expanduser().resolve() / "mobile-releases" / "android"
    )
    try:
        version_name, version_code = read_apk_version(apk)
        release = repository.publish(
            apk,
            version_name=version_name,
            version_code=version_code,
            min_supported_version_code=min_version_code,
            release_notes=notes,
        )
    except (LookupError, OSError, ValueError) as exc:
        print(f"Hosted Android publication failed: {exc}", file=sys.stderr)
        return 2
    _print_mobile_release(repository, release)
    return 0


def _mobile_latest(root: Path) -> int:
    repository = AndroidReleaseRepository(
        root.expanduser().resolve() / "mobile-releases" / "android"
    )
    try:
        release = repository.latest()
        assert release is not None
    except LookupError as exc:
        print(f"Hosted Android release unavailable: {exc}", file=sys.stderr)
        return 2
    _print_mobile_release(repository, release)
    return 0


def _print_mobile_release(
    repository: AndroidReleaseRepository,
    release: AndroidRelease,
) -> None:
    print(f"version_name={release.version_name}")
    print(f"version_code={release.version_code}")
    print(f"min_supported_version_code={release.min_supported_version_code}")
    print(f"size_bytes={release.size_bytes}")
    print(f"sha256={release.sha256}")
    print(f"package={repository.package_path(release)}")
    print("download_path=/downloads/android/latest.apk")


def _backup(root: Path, output: Path) -> int:
    source = root.expanduser().resolve()
    target = output.expanduser().resolve()
    if target.exists():
        print("Backup output already exists", file=sys.stderr)
        return 2
    try:
        control = source / "control.db"
        signing_key = source / "hub-signing.key"
        if not control.is_file() or not signing_key.is_file() or signing_key.is_symlink():
            raise ValueError("Hosted root is incomplete")
        target.mkdir(parents=True, mode=0o700)
        _sqlite_backup(control, target / "control.db")
        shutil.copyfile(signing_key, target / "hub-signing.key")
        (target / "hub-signing.key").chmod(0o600)
        with sqlite3.connect(target / "control.db") as db:
            rows = db.execute(
                "SELECT workspace_id FROM hosted_workspaces ORDER BY workspace_id"
            ).fetchall()
        workspaces: list[dict[str, str]] = []
        for (workspace_id,) in rows:
            workspace = str(workspace_id)
            if not re.fullmatch(r"ws_[A-Za-z0-9_-]{12,96}", workspace):
                raise ValueError("Hosted control database contains an invalid Workspace ID")
            database = source / "tenants" / workspace / "hub.db"
            if not database.is_file():
                raise ValueError(f"Workspace database is missing: {workspace}")
            destination = target / "tenants" / workspace / "hub.db"
            destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            _sqlite_backup(database, destination)
            workspaces.append(
                {"workspace_id": workspace, "sha256": _sha256(destination)}
            )
        mobile_release_files = _backup_mobile_releases(source, target)
        manifest = {
            "version": _BACKUP_VERSION,
            "created_at": time.time(),
            "control_sha256": _sha256(target / "control.db"),
            "signing_key_sha256": _sha256(target / "hub-signing.key"),
            "workspaces": workspaces,
            "mobile_release_files": mobile_release_files,
        }
        (target / "manifest.json").write_text(
            json.dumps(manifest, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        (target / "manifest.json").chmod(0o600)
    except (OSError, sqlite3.Error, ValueError) as exc:
        if target.exists():
            shutil.rmtree(target)
        print(f"Hosted backup failed: {exc}", file=sys.stderr)
        return 1
    print(str(target))
    return 0


def _restore(backup: Path, root: Path) -> int:
    source = backup.expanduser().resolve()
    target = root.expanduser().resolve()
    if target.exists() and any(target.iterdir()):
        print("Restore root must not exist or must be empty", file=sys.stderr)
        return 2
    try:
        manifest = json.loads((source / "manifest.json").read_text(encoding="utf-8"))
        if manifest.get("version") != _BACKUP_VERSION:
            raise ValueError("Hosted backup version is unsupported")
        files = [
            (source / "control.db", str(manifest["control_sha256"])),
            (source / "hub-signing.key", str(manifest["signing_key_sha256"])),
        ]
        for item in manifest["workspaces"]:
            workspace = str(item["workspace_id"])
            if not re.fullmatch(r"ws_[A-Za-z0-9_-]{12,96}", workspace):
                raise ValueError("Hosted backup contains an invalid Workspace ID")
            files.append((source / "tenants" / workspace / "hub.db", str(item["sha256"])))
        mobile_release_files = manifest["mobile_release_files"]
        if not isinstance(mobile_release_files, list):
            raise TypeError("Hosted backup mobile release manifest is invalid")
        seen_release_files: set[str] = set()
        for item in mobile_release_files:
            name = str(item["name"])
            if not _mobile_release_file_name(name) or name in seen_release_files:
                raise ValueError("Hosted backup contains an invalid mobile release file")
            seen_release_files.add(name)
            files.append(
                (
                    source / "mobile-releases" / "android" / name,
                    str(item["sha256"]),
                )
            )
        if any(not path.is_file() or _sha256(path) != digest for path, digest in files):
            raise ValueError("Hosted backup integrity verification failed")
        target.mkdir(parents=True, exist_ok=True, mode=0o700)
        shutil.copyfile(source / "control.db", target / "control.db")
        shutil.copyfile(source / "hub-signing.key", target / "hub-signing.key")
        (target / "hub-signing.key").chmod(0o600)
        _sqlite_integrity(target / "control.db")
        for item in manifest["workspaces"]:
            workspace = str(item["workspace_id"])
            destination = target / "tenants" / workspace / "hub.db"
            destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            shutil.copyfile(source / "tenants" / workspace / "hub.db", destination)
            _sqlite_integrity(destination)
        release_root = target / "mobile-releases" / "android"
        for item in mobile_release_files:
            name = str(item["name"])
            destination = release_root / name
            destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            shutil.copyfile(source / "mobile-releases" / "android" / name, destination)
            destination.chmod(0o600)
        if mobile_release_files:
            _validate_mobile_releases(release_root)
    except (KeyError, OSError, sqlite3.Error, TypeError, ValueError) as exc:
        if target.exists():
            shutil.rmtree(target)
        print(f"Hosted restore failed: {exc}", file=sys.stderr)
        return 1
    print(str(target))
    return 0


def _sqlite_backup(source: Path, target: Path) -> None:
    with (
        sqlite3.connect(f"file:{source}?mode=ro", uri=True) as source_db,
        sqlite3.connect(target) as target_db,
    ):
        source_db.backup(target_db)
    target.chmod(0o600)
    _sqlite_integrity(target)


def _backup_mobile_releases(source: Path, target: Path) -> list[dict[str, str]]:
    source_root = source / "mobile-releases" / "android"
    if not source_root.exists():
        return []
    if source_root.is_symlink() or not source_root.is_dir():
        raise ValueError("Hosted mobile release root is invalid")
    _validate_mobile_releases(source_root)
    files: list[dict[str, str]] = []
    for item in sorted(source_root.iterdir(), key=lambda path: path.name):
        if (
            not _mobile_release_file_name(item.name)
            or item.is_symlink()
            or not item.is_file()
        ):
            raise ValueError("Hosted mobile release root contains an invalid file")
        destination = target / "mobile-releases" / "android" / item.name
        destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        shutil.copyfile(item, destination)
        destination.chmod(0o600)
        files.append({"name": item.name, "sha256": _sha256(destination)})
    if files:
        _validate_mobile_releases(target / "mobile-releases" / "android")
    return files


def _validate_mobile_releases(root: Path) -> None:
    repository = AndroidReleaseRepository(root)
    manifests = sorted(
        path
        for path in root.iterdir()
        if path.name != "latest.json" and re.fullmatch(r"[1-9][0-9]*\.json", path.name)
    )
    packages = {
        path.name
        for path in root.iterdir()
        if re.fullmatch(r"knoa-[1-9][0-9]*\.apk", path.name)
    }
    if not manifests and not packages and not (root / "latest.json").exists():
        return
    latest = repository.latest()
    assert latest is not None
    expected_packages: set[str] = set()
    releases = []
    for manifest in manifests:
        version_code = int(manifest.stem)
        release = repository.get(version_code)
        if release.version_code != version_code:
            raise ValueError("Hosted mobile release manifest version is inconsistent")
        package = repository.package_path(release)
        if _sha256(package) != release.sha256:
            raise ValueError("Hosted mobile release package digest is inconsistent")
        expected_packages.add(release.file_name)
        releases.append(release)
    if packages != expected_packages or not releases:
        raise ValueError("Hosted mobile release repository is incomplete")
    newest = max(releases, key=lambda release: release.version_code)
    if latest != newest:
        raise ValueError("Hosted latest mobile release is inconsistent")


def _mobile_release_file_name(name: str) -> bool:
    return bool(
        name == "latest.json"
        or re.fullmatch(r"[1-9][0-9]*\.json", name)
        or re.fullmatch(r"knoa-[1-9][0-9]*\.apk", name)
    )


def _sqlite_integrity(path: Path) -> None:
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as db:
        result = db.execute("PRAGMA integrity_check").fetchone()
    if result is None or result[0] != "ok":
        raise ValueError(f"SQLite integrity check failed: {path.name}")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _url(value: str, *, allow_http_loopback: bool) -> str:
    normalized = value.strip().rstrip("/")
    parsed = urlsplit(normalized)
    if parsed.scheme == "https" and parsed.netloc and not parsed.path:
        return normalized
    if (
        allow_http_loopback
        and parsed.scheme == "http"
        and parsed.hostname in {"127.0.0.1", "localhost", "::1"}
        and parsed.netloc
        and not parsed.path
    ):
        return normalized
    raise ValueError("Hosted Hub URL must be HTTPS; admin endpoint may be loopback HTTP")


def _print_qr(payload: str) -> None:
    import qrcode

    code = qrcode.QRCode(border=1)
    code.add_data(payload)
    code.make(fit=True)
    code.print_ascii(invert=True)


if __name__ == "__main__":
    raise SystemExit(main())
