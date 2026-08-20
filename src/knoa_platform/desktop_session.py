"""Recover the current user's graphical-session environment for desktop tools."""
from __future__ import annotations

import os
import stat
import subprocess
import sys
import threading
from dataclasses import dataclass
from pathlib import Path

if sys.platform.startswith("linux"):
    import pwd
else:  # pragma: no cover - exercised by Windows CI
    pwd = None


DESKTOP_TOOL_NAMES = frozenset(
    {
        "clipboard",
        "notify",
        "mouse",
        "press_key",
        "type_text",
        "hotkey",
        "screenshot",
        "windows",
    }
)

_DESKTOP_ENV_KEYS = frozenset(
    {
        "DISPLAY",
        "XAUTHORITY",
        "WAYLAND_DISPLAY",
        "XDG_SESSION_TYPE",
        "XDG_RUNTIME_DIR",
        "DBUS_SESSION_BUS_ADDRESS",
    }
)
_ENVIRONMENT_LOCK = threading.Lock()


class DesktopSessionError(RuntimeError):
    """Raised when a desktop tool cannot safely locate a graphical session."""


@dataclass(frozen=True)
class _GraphicalSession:
    session_id: str
    leader: int
    session_type: str


def is_desktop_tool(tool_name: str) -> bool:
    return tool_name in DESKTOP_TOOL_NAMES


def ensure_desktop_session(tool_name: str) -> None:
    """Ensure desktop tools inherit the active local GUI session.

    Services often start outside the graphical login and therefore lack the
    small set of environment variables required by X11 or Wayland clients.
    Recovery is deliberately limited to the current user's active, local
    graphical session and never imports arbitrary process environment values.
    """
    if not is_desktop_tool(tool_name):
        return
    if os.name == "nt":
        from knoa_platform.desktop_companion import (
            DesktopCompanionError,
            companion_available,
            desktop_companion_required,
        )

        if desktop_companion_required() and not companion_available():
            raise DesktopCompanionError(
                "Knoa Desktop Companion is unavailable in the active Windows session"
            )
        return
    if not sys.platform.startswith("linux"):
        return

    with _ENVIRONMENT_LOCK:
        if _desktop_environment_is_usable(os.environ):
            return
        candidate = _recover_desktop_environment()

        # Build and validate the complete candidate before changing the
        # process environment.  The lock gives desktop calls one publication
        # boundary and prevents concurrent recovery attempts from interleaving.
        published = {key: value for key, value in candidate.items() if key in _DESKTOP_ENV_KEYS and value}
        if not _desktop_environment_is_usable(published):
            raise DesktopSessionError("Recovered graphical session environment is incomplete")
        for key in _DESKTOP_ENV_KEYS:
            os.environ.pop(key, None)
        os.environ.update(published)


def _desktop_environment_is_usable(environment: os._Environ[str] | dict[str, str]) -> bool:
    if str(environment.get("DISPLAY", "")).strip():
        return True
    return bool(
        str(environment.get("WAYLAND_DISPLAY", "")).strip()
        and str(environment.get("XDG_RUNTIME_DIR", "")).strip()
    )


def _recover_desktop_environment() -> dict[str, str]:
    sessions = _list_active_graphical_sessions()
    if not sessions:
        raise DesktopSessionError("No active local graphical session found for the current user")
    if len(sessions) != 1:
        raise DesktopSessionError("Found multiple active local graphical sessions; refusing ambiguous recovery")

    session = sessions[0]
    try:
        leader_environment = _read_process_environment(session.leader)
    except DesktopSessionError:
        # GDM commonly reports its root-owned session worker as Leader.  A
        # user service cannot read that process environment, but can still
        # recover safely from one unambiguous local X socket and an authority
        # file owned by the current user.
        leader_environment = {}
    candidate = {
        key: value.strip()
        for key, value in leader_environment.items()
        if key in _DESKTOP_ENV_KEYS and value.strip()
    }
    candidate.setdefault("XDG_SESSION_TYPE", session.session_type)

    if not candidate.get("DISPLAY"):
        try:
            candidate["DISPLAY"] = _unique_x11_display()
        except DesktopSessionError:
            if not (
                candidate.get("WAYLAND_DISPLAY")
                and candidate.get("XDG_RUNTIME_DIR")
            ):
                raise

    if candidate.get("DISPLAY"):
        candidate["XAUTHORITY"] = _select_xauthority(candidate)

    return candidate


def _run_loginctl(*arguments: str) -> str:
    try:
        result = subprocess.run(
            ["loginctl", *arguments],
            check=False,
            capture_output=True,
            text=True,
            timeout=3,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise DesktopSessionError(f"Unable to query graphical sessions: {exc}") from exc
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "loginctl failed").strip()
        raise DesktopSessionError(f"Unable to query graphical sessions: {detail[:200]}")
    return result.stdout


def _parse_properties(output: str) -> dict[str, str]:
    return dict(line.split("=", 1) for line in output.splitlines() if "=" in line)


def _list_active_graphical_sessions() -> list[_GraphicalSession]:
    listing = _run_loginctl("list-sessions", "--no-legend", "--no-pager")
    current_uid = str(os.getuid())
    sessions: list[_GraphicalSession] = []
    for line in listing.splitlines():
        fields = line.split()
        if len(fields) < 2 or fields[1] != current_uid:
            continue
        session_id = fields[0]
        try:
            properties = _parse_properties(
                _run_loginctl(
                    "show-session",
                    session_id,
                    "-p",
                    "Active",
                    "-p",
                    "Remote",
                    "-p",
                    "Type",
                    "-p",
                    "Leader",
                    "--no-pager",
                )
            )
        except DesktopSessionError:
            continue
        session_type = properties.get("Type", "").lower()
        if properties.get("Active") != "yes":
            continue
        if properties.get("Remote") == "yes":
            continue
        if session_type not in {"x11", "wayland"}:
            continue
        try:
            leader = int(properties.get("Leader", ""))
        except ValueError:
            continue
        if leader <= 0:
            continue
        sessions.append(_GraphicalSession(session_id, leader, session_type))
    return sessions


def _read_process_environment(leader: int) -> dict[str, str]:
    process_dir = Path("/proc") / str(leader)
    environment_path = process_dir / "environ"
    try:
        if process_dir.stat().st_uid != os.getuid():
            raise DesktopSessionError("Graphical session leader does not belong to the current user")
        raw = environment_path.read_bytes()
    except DesktopSessionError:
        raise
    except OSError as exc:
        raise DesktopSessionError(f"Unable to read graphical session environment: {exc}") from exc

    environment: dict[str, str] = {}
    for entry in raw.split(b"\0"):
        if not entry or b"=" not in entry:
            continue
        key, value = entry.split(b"=", 1)
        decoded_key = key.decode("utf-8", errors="ignore")
        if decoded_key not in _DESKTOP_ENV_KEYS:
            continue
        environment[decoded_key] = value.decode("utf-8", errors="surrogateescape")
    return environment


def _unique_x11_display() -> str:
    socket_dir = Path("/tmp/.X11-unix")
    displays: list[str] = []
    try:
        entries = list(socket_dir.iterdir())
    except OSError as exc:
        raise DesktopSessionError(f"Unable to inspect X11 displays: {exc}") from exc
    for entry in entries:
        suffix = entry.name[1:] if entry.name.startswith("X") else ""
        if not suffix.isdigit():
            continue
        try:
            if stat.S_ISSOCK(entry.stat().st_mode):
                displays.append(f":{int(suffix)}")
        except OSError:
            continue
    displays = sorted(set(displays))
    if not displays:
        raise DesktopSessionError("No local X11 display socket found")
    if len(displays) != 1:
        raise DesktopSessionError("Found multiple local X11 displays; refusing ambiguous recovery")
    return displays[0]


def _select_xauthority(environment: dict[str, str]) -> str:
    uid = os.getuid()
    candidates: list[Path] = []
    configured = environment.get("XAUTHORITY", "").strip()
    if configured:
        candidates.append(Path(configured).expanduser())

    runtime_values = [environment.get("XDG_RUNTIME_DIR", "").strip(), f"/run/user/{uid}"]
    for runtime_value in runtime_values:
        if runtime_value:
            candidates.append(Path(runtime_value) / "gdm" / "Xauthority")

    try:
        assert pwd is not None
        candidates.append(Path(pwd.getpwuid(uid).pw_dir) / ".Xauthority")
    except KeyError:
        pass

    seen: set[Path] = set()
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        try:
            metadata = candidate.stat()
        except OSError:
            continue
        if metadata.st_uid != uid or not stat.S_ISREG(metadata.st_mode):
            continue
        if os.access(candidate, os.R_OK):
            return str(candidate)
    raise DesktopSessionError("No readable X11 authority file found for the current user")
