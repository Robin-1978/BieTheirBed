"""Locate the OpenCode background service of the current OS user.

``opencode serve --service`` registers itself in
``$XDG_STATE_HOME/opencode/service.json`` (``~/.local/state/opencode`` by
default) with its URL, pid and the Basic-Auth password (user name
``opencode``).  OpenCode's own clients read that file to find the service, so
KNOA follows the same registration instead of asking for a second copy of the
password: whoever runs OpenCode as this OS user is who KNOA talks to.

The registration is a credential, so it is only trusted when it is a private
regular file owned by the current user and points to a plain-HTTP loopback
address.
"""

from __future__ import annotations

import errno
import json
import os
import stat
from collections.abc import Callable, Mapping
from pathlib import Path
from urllib.parse import urlsplit

from knoa_codex_agent.coding_sessions import (
    CodingSessionProviderError,
    OpenCodeEndpoint,
)
from knoa_platform.service.processes import process_exists

_MAX_RECORD_BYTES = 64 * 1024
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
_USERNAME = "opencode"


class OpenCodeServiceRecordError(CodingSessionProviderError):
    """The OpenCode service registration cannot be trusted or understood.

    Messages never contain the registration's content.
    """


def default_record_path(
    environ: Mapping[str, str] | None = None, *, home: Path | None = None
) -> Path:
    """Path OpenCode registers its background service in (same rule as OpenCode)."""

    env = os.environ if environ is None else environ
    state_home = env.get("XDG_STATE_HOME") or ""
    root = Path(state_home) if state_home else (home or Path.home()) / ".local" / "state"
    return root / "opencode" / "service.json"


class OpenCodeServiceLocator:
    """Resolve the running OpenCode service from its registration file.

    The file is read on every ``locate()`` call so a restarted service (new
    port, pid or password) is followed without restarting KNOA.
    """

    def __init__(
        self,
        path: str | Path | None = None,
        *,
        uid: int | None = None,
        pid_alive: Callable[[int], bool] = process_exists,
    ) -> None:
        self._path = Path(path) if path is not None else None
        self._uid = uid
        self._pid_alive = pid_alive

    def locate(self) -> OpenCodeEndpoint | None:
        """Return the live service, or ``None`` when OpenCode is not running.

        A registration that exists but cannot be trusted raises
        ``OpenCodeServiceRecordError`` instead of being silently ignored.
        """

        text = self._read(self._path or default_record_path())
        if text is None:
            return None
        endpoint = self._parse(text)
        if not self._pid_alive(endpoint.pid):
            return None
        return endpoint

    def _read(self, path: Path) -> str | None:
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
        try:
            descriptor = os.open(path, flags)
        except FileNotFoundError:
            return None
        except OSError as exc:
            if exc.errno == errno.ELOOP:
                raise OpenCodeServiceRecordError(
                    "OpenCode registration must not be a symlink"
                ) from exc
            raise OpenCodeServiceRecordError(
                "OpenCode registration cannot be opened"
            ) from exc
        try:
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode):
                raise OpenCodeServiceRecordError(
                    "OpenCode registration must be a regular file"
                )
            if info.st_size > _MAX_RECORD_BYTES:
                raise OpenCodeServiceRecordError("OpenCode registration is too large")
            if os.name != "nt":
                expected = os.getuid() if self._uid is None else self._uid
                if info.st_uid != expected:
                    raise OpenCodeServiceRecordError(
                        "OpenCode registration is not owned by the current user"
                    )
                if info.st_mode & 0o077:
                    raise OpenCodeServiceRecordError(
                        "OpenCode registration must be private (mode 600)"
                    )
            data = os.read(descriptor, _MAX_RECORD_BYTES + 1)
        finally:
            os.close(descriptor)
        if len(data) > _MAX_RECORD_BYTES:
            raise OpenCodeServiceRecordError("OpenCode registration is too large")
        try:
            return data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise OpenCodeServiceRecordError(
                "OpenCode registration is not valid UTF-8"
            ) from exc

    @staticmethod
    def _parse(text: str) -> OpenCodeEndpoint:
        try:
            record = json.loads(text)
        except ValueError as exc:
            raise OpenCodeServiceRecordError(
                "OpenCode registration is not valid JSON"
            ) from exc
        if not isinstance(record, dict):
            raise OpenCodeServiceRecordError(
                "OpenCode registration must be a JSON object"
            )
        password = record.get("password")
        if not isinstance(password, str) or not password:
            raise OpenCodeServiceRecordError("OpenCode registration has no password")
        pid = record.get("pid")
        if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
            raise OpenCodeServiceRecordError("OpenCode registration has no valid pid")
        return OpenCodeEndpoint(
            base_url=_loopback_base_url(record.get("url")),
            password=password,
            username=_USERNAME,
            pid=pid,
        )


def _loopback_base_url(value: object) -> str:
    refusal = OpenCodeServiceRecordError(
        "OpenCode registration must point to a plain http loopback URL"
    )
    if not isinstance(value, str) or not value:
        raise refusal
    try:
        parts = urlsplit(value)
        parts.port  # noqa: B018 - raises ValueError for an invalid port
    except ValueError as exc:
        raise refusal from exc
    if parts.scheme != "http" or parts.hostname not in _LOOPBACK_HOSTS:
        raise refusal
    if parts.username is not None or parts.password is not None:
        raise refusal
    if parts.path not in ("", "/") or parts.query or parts.fragment:
        raise refusal
    return f"http://{parts.netloc}"
