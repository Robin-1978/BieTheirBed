"""Local RFC 6238 TOTP verification and desktop-session unlock broker."""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import re
import secrets
import stat
import subprocess
import threading
import time
from pathlib import Path


class TotpUnlockBroker:
    """Verify a phone TOTP and unlock only the current user's GUI session."""

    def __init__(self, secret_file: str | Path, allowed_open_ids: list[str] | tuple[str, ...], *, enabled: bool = False, period: int = 30, max_attempts: int = 3, lockout_seconds: int = 300) -> None:
        self._path = Path(secret_file).expanduser()
        self._allowed = frozenset(str(v).strip() for v in allowed_open_ids if str(v).strip())
        self._enabled = bool(enabled)
        self._period = max(15, int(period))
        self._max_attempts = max(1, int(max_attempts))
        self._lockout_seconds = max(30, int(lockout_seconds))
        self._lock = threading.Lock()
        self._failures: dict[str, tuple[int, float]] = {}
        self._last_counter: dict[str, int] = {}

    @staticmethod
    def generate_secret() -> str:
        return base64.b32encode(secrets.token_bytes(20)).decode("ascii").rstrip("=")

    @staticmethod
    def provisioning_uri(secret: str, account: str = "robin") -> str:
        from urllib.parse import quote
        return f"otpauth://totp/{quote(f'PC Assistant:{account}')}?secret={secret}&issuer={quote('PC Assistant')}&algorithm=SHA1&digits=6&period=30"

    @classmethod
    def write_secret(cls, path: str | Path) -> tuple[str, str]:
        target = Path(path).expanduser()
        target.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(target.parent, stat.S_IRWXU)
        secret = cls.generate_secret()
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            os.write(fd, (secret + "\n").encode("ascii"))
        finally:
            os.close(fd)
        return secret, cls.provisioning_uri(secret)

    @staticmethod
    def _counter(now: float, period: int) -> int:
        return int(now // period)

    @classmethod
    def _code(cls, secret: str, counter: int) -> str:
        padded = secret.strip().upper() + "=" * ((8 - len(secret.strip()) % 8) % 8)
        key = base64.b32decode(padded, casefold=True)
        digest = hmac.new(key, counter.to_bytes(8, "big"), hashlib.sha1).digest()
        offset = digest[-1] & 0x0F
        value = int.from_bytes(digest[offset : offset + 4], "big") & 0x7FFFFFFF
        return f"{value % 1_000_000:06d}"

    def _read_secret(self) -> str:
        if not self._path.is_file():
            raise FileNotFoundError(f"TOTP secret is not configured: {self._path}")
        if stat.S_IMODE(self._path.stat().st_mode) & 0o077:
            raise PermissionError("TOTP secret file must be readable only by its owner (chmod 600)")
        secret = self._path.read_text(encoding="ascii").strip().upper()
        if not secret:
            raise ValueError("TOTP secret is empty")
        return secret

    def verify_and_unlock(self, open_id: str, code: str) -> tuple[bool, str]:
        user = str(open_id).strip()
        normalized = str(code).strip()
        if not self._enabled:
            return False, "Remote unlock is disabled"
        if user not in self._allowed:
            return False, "This Feishu account is not authorized for remote unlock"
        if re.fullmatch(r"[0-9]{6}", normalized) is None:
            return False, "TOTP must be a 6-digit code"
        now = time.time()
        with self._lock:
            failures, locked_until = self._failures.get(user, (0, 0.0))
            if locked_until > now:
                return False, "Too many failed attempts; try again later"
            if locked_until:
                failures = 0
                self._failures.pop(user, None)
        try:
            secret = self._read_secret()
        except (OSError, ValueError, PermissionError) as exc:
            return False, str(exc)
        counter = self._counter(now, self._period)
        matched: int | None = None
        for candidate in (counter - 1, counter, counter + 1):
            if candidate < 0:
                continue
            if hmac.compare_digest(self._code(secret, candidate), normalized):
                matched = candidate
                break
        with self._lock:
            if matched is None:
                failures += 1
                self._failures[user] = (failures, now + self._lockout_seconds if failures >= self._max_attempts else 0.0)
                return False, "Invalid or expired TOTP"
            if self._last_counter.get(user) == matched:
                return False, "This TOTP code was already used"
            self._last_counter[user] = matched
            self._failures.pop(user, None)
        session_id = self._current_graphical_session()
        if not session_id:
            return False, "No active graphical session found"
        try:
            result = subprocess.run(["loginctl", "unlock-session", session_id], check=False, capture_output=True, text=True, timeout=5)
        except OSError as exc:
            return False, f"Unlock request failed: {exc}"
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "unlock rejected").strip()
            return False, f"Unlock request rejected: {detail[:160]}"
        return True, "Desktop unlock requested"

    @staticmethod
    def _current_graphical_session() -> str:
        explicit = os.environ.get("XDG_SESSION_ID", "").strip()
        if explicit:
            return explicit
        try:
            result = subprocess.run(["loginctl", "list-sessions", "--no-legend"], check=False, capture_output=True, text=True, timeout=3)
        except OSError:
            return ""
        uid = str(os.getuid())
        for line in result.stdout.splitlines():
            fields = line.split()
            if len(fields) < 3 or fields[1] != uid:
                continue
            session = fields[0]
            try:
                props = subprocess.run(["loginctl", "show-session", session, "-p", "Type", "-p", "Remote"], check=False, capture_output=True, text=True, timeout=3).stdout
            except OSError:
                continue
            values = dict(line.split("=", 1) for line in props.splitlines() if "=" in line)
            if values.get("Type") in {"x11", "wayland"} and values.get("Remote") != "yes":
                return session
        return ""
