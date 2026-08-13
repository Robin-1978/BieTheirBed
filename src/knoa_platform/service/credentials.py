"""Private credential management for the loopback Core WebSocket service."""
from __future__ import annotations

import os
import secrets
import stat
import base64
import hashlib
import hmac
import time
from pathlib import Path

from knoa_platform.runtime import RuntimePaths


_MAX_TOKEN_BYTES = 4096
_SIGNED_CREDENTIAL_TTL_SECONDS = 60


def resolve_local_service_token(paths: RuntimePaths) -> str:
    """Return the private, persistent credential for local Core clients."""
    return _load_or_create(paths.config / "service.token")


def issue_principal_credential(signing_key: str, principal: str) -> str:
    """Issue a short-lived credential for a trusted local adapter principal."""
    normalized = principal.strip()
    if not normalized or len(normalized) > 256:
        raise ValueError("Signed principal must contain 1 to 256 characters")
    encoded = base64.urlsafe_b64encode(normalized.encode("utf-8")).decode("ascii")
    timestamp = str(int(time.time()))
    nonce = secrets.token_urlsafe(12)
    payload = f"v1.{encoded}.{timestamp}.{nonce}"
    signature = hmac.new(
        signing_key.encode("utf-8"),
        payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return f"{payload}.{signature}"


def verify_principal_credential(
    signing_key: str,
    credential: str,
    *,
    now: int | None = None,
) -> str | None:
    """Verify one adapter credential and return its signed principal."""
    parts = credential.split(".")
    if len(parts) != 5 or parts[0] != "v1":
        return None
    version, encoded, timestamp_text, nonce, supplied = parts
    try:
        timestamp = int(timestamp_text)
    except ValueError:
        return None
    current = int(time.time()) if now is None else now
    if abs(current - timestamp) > _SIGNED_CREDENTIAL_TTL_SECONDS:
        return None
    payload = f"{version}.{encoded}.{timestamp_text}.{nonce}"
    expected = hmac.new(
        signing_key.encode("utf-8"),
        payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(expected, supplied):
        return None
    try:
        padding = "=" * (-len(encoded) % 4)
        principal = base64.urlsafe_b64decode(encoded + padding).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return None
    principal = principal.strip()
    return principal if 0 < len(principal) <= 256 else None


def _load_or_create(path: Path) -> str:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    parent_stat = path.parent.stat()
    if parent_stat.st_uid != os.geteuid():
        raise RuntimeError(f"Service credential directory has the wrong owner: {path.parent}")
    path.parent.chmod(0o700)

    token = secrets.token_urlsafe(32)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags, 0o600)
    except FileExistsError:
        return _read_private_token(path)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(token + "\n")
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    path.chmod(0o600)
    return token


def _read_private_token(path: Path) -> str:
    metadata = path.lstat()
    if not stat.S_ISREG(metadata.st_mode):
        raise RuntimeError(f"Service credential must be a regular file: {path}")
    if metadata.st_uid != os.geteuid():
        raise RuntimeError(f"Service credential has the wrong owner: {path}")
    if stat.S_IMODE(metadata.st_mode) & 0o077:
        raise RuntimeError(f"Service credential must be owner-only: {path}")
    if metadata.st_size > _MAX_TOKEN_BYTES:
        raise RuntimeError(f"Service credential exceeds {_MAX_TOKEN_BYTES} bytes: {path}")
    token = path.read_text(encoding="utf-8").strip()
    if not token:
        raise RuntimeError(f"Service credential is empty: {path}")
    return token
