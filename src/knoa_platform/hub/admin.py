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
from knoa_platform.node_hub import NodeHubService, NodeHubStore
from knoa_platform.node_identity import NodeIdentityStore
from knoa_platform.runtime import RuntimePaths

_BACKUP_VERSION = "knoa-hosted-backup-v1"


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
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "backup":
        return _backup(Path(args.root), Path(args.output))
    if args.command == "restore":
        return _restore(Path(args.backup), Path(args.root))
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
            destination.parent.mkdir(parents=True, mode=0o700)
            _sqlite_backup(database, destination)
            workspaces.append(
                {"workspace_id": workspace, "sha256": _sha256(destination)}
            )
        manifest = {
            "version": _BACKUP_VERSION,
            "created_at": time.time(),
            "control_sha256": _sha256(target / "control.db"),
            "signing_key_sha256": _sha256(target / "hub-signing.key"),
            "workspaces": workspaces,
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
            destination.parent.mkdir(parents=True, mode=0o700)
            shutil.copyfile(source / "tenants" / workspace / "hub.db", destination)
            _sqlite_integrity(destination)
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
