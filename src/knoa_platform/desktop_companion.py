"""Desktop-session companion and authenticated local IPC client.

Windows services execute in Session 0 and must never call desktop APIs
directly.  The companion runs in the signed-in user's session and exposes only
the fixed set of built-in desktop tools over an authenticated named pipe.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import ctypes
import json
import os
from multiprocessing.connection import Client, Listener
from pathlib import Path
from typing import Any


_MAX_REQUEST_BYTES = 1024 * 1024
_MAX_RESPONSE_BYTES = 64 * 1024 * 1024
_COMPANION_PROCESS_ENV = "KNOA_DESKTOP_COMPANION_PROCESS"
_TOKEN_FILE_ENV = "KNOA_DESKTOP_COMPANION_TOKEN_FILE"
_ALLOWED_TOOLS = frozenset(
    {
        "clipboard",
        "hotkey",
        "mouse",
        "notify",
        "press_key",
        "screenshot",
        "type_text",
        "windows",
    }
)


class DesktopCompanionError(RuntimeError):
    """Raised when the interactive desktop worker cannot serve a request."""


def _windows_process_session_id(process_id: int | None = None) -> int:
    if os.name != "nt":
        raise DesktopCompanionError("Desktop Companion named pipes require Windows")
    session_id = ctypes.c_ulong()
    pid = os.getpid() if process_id is None else process_id
    if not ctypes.windll.kernel32.ProcessIdToSessionId(pid, ctypes.byref(session_id)):
        raise DesktopCompanionError("Unable to resolve the Windows process session")
    return int(session_id.value)


def running_in_companion() -> bool:
    return os.environ.get(_COMPANION_PROCESS_ENV) == "1"


def desktop_companion_required() -> bool:
    """Return true only for a Windows process isolated from the user desktop."""
    if os.name != "nt" or running_in_companion():
        return False
    return _windows_process_session_id() == 0


def _active_console_session_id() -> int:
    if os.name != "nt":
        raise DesktopCompanionError("Desktop Companion is only supported on Windows")
    session_id = int(ctypes.windll.kernel32.WTSGetActiveConsoleSessionId())
    if session_id == 0xFFFFFFFF:
        raise DesktopCompanionError("No signed-in Windows desktop session is active")
    return session_id


def _pipe_address(session_id: int) -> str:
    if session_id <= 0:
        raise DesktopCompanionError("No interactive Windows desktop session is available")
    return rf"\\.\pipe\knoa-desktop-{session_id}"


def _read_authkey(token_file: str | Path | None = None) -> bytes:
    selected = str(token_file or os.environ.get(_TOKEN_FILE_ENV, "")).strip()
    if not selected:
        raise DesktopCompanionError("Desktop Companion token is not configured")
    try:
        token = Path(selected).read_text(encoding="utf-8").strip().encode("ascii")
    except (OSError, UnicodeError) as exc:
        raise DesktopCompanionError("Desktop Companion token is unavailable") from exc
    if len(token) < 32:
        raise DesktopCompanionError("Desktop Companion token is invalid")
    return token


def _json_bytes(value: dict[str, Any]) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def invoke_desktop_companion(
    tool_name: str,
    arguments: dict[str, Any],
    *,
    token_file: str | Path | None = None,
) -> dict[str, Any]:
    if tool_name not in _ALLOWED_TOOLS:
        raise DesktopCompanionError("Desktop tool is not exposed by the Companion")
    session_id = _active_console_session_id()
    request = _json_bytes(
        {"version": 1, "session_id": session_id, "tool": tool_name, "arguments": arguments}
    )
    if len(request) > _MAX_REQUEST_BYTES:
        raise DesktopCompanionError("Desktop tool request is too large")
    try:
        connection = Client(
            _pipe_address(session_id),
            family="AF_PIPE",
            authkey=_read_authkey(token_file),
        )
        try:
            connection.send_bytes(request)
            response = connection.recv_bytes(_MAX_RESPONSE_BYTES)
        finally:
            connection.close()
    except (OSError, EOFError, AuthenticationError) as exc:
        raise DesktopCompanionError(
            "Knoa Desktop Companion is not running in the active Windows session"
        ) from exc
    try:
        parsed = json.loads(response)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DesktopCompanionError("Desktop Companion returned an invalid response") from exc
    if not isinstance(parsed, dict):
        raise DesktopCompanionError("Desktop Companion returned an invalid response")
    if parsed.get("error"):
        raise DesktopCompanionError(str(parsed["error"]))
    result = parsed.get("result")
    if not isinstance(result, dict):
        raise DesktopCompanionError("Desktop Companion returned an invalid tool result")
    return result


# multiprocessing.connection exposes AuthenticationError from this module on
# CPython but importing it directly keeps exception handling portable.
try:
    from multiprocessing.context import AuthenticationError
except ImportError:  # pragma: no cover - supported CPython always provides it
    AuthenticationError = OSError  # type: ignore[misc,assignment]


def companion_available() -> bool:
    try:
        result = invoke_desktop_companion("screenshot", {"probe": True})
    except DesktopCompanionError:
        return False
    return result.get("available") is True


def desktop_companion_status() -> dict[str, Any]:
    if os.name != "nt":
        return {"mode": "direct", "available": True}
    try:
        required = desktop_companion_required()
    except DesktopCompanionError as exc:
        return {"mode": "companion", "available": False, "detail": str(exc)}
    if not required:
        return {"mode": "direct", "available": True}
    available = companion_available()
    return {
        "mode": "companion",
        "available": available,
        "detail": "ready" if available else "not_running_or_no_active_session",
    }


def _capture_screenshot(arguments: dict[str, Any]) -> dict[str, Any]:
    if arguments.get("probe") is True:
        return {"available": True}
    import io

    import mss
    from PIL import Image

    with mss.mss() as capture:
        shot = capture.grab(capture.monitors[0])
        image = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
        max_width = 3200
        if image.width > max_width:
            image = image.resize(
                (max_width, max(1, round(image.height * max_width / image.width))),
                Image.Resampling.LANCZOS,
            )
        stream = io.BytesIO()
        image.save(stream, format="JPEG", quality=85, optimize=True, progressive=True)
    return {
        "media_type": "image/jpeg",
        "content_base64": base64.b64encode(stream.getvalue()).decode("ascii"),
    }


async def _execute_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    if tool_name == "screenshot":
        return await asyncio.to_thread(_capture_screenshot, arguments)

    from knoa_platform.tools.clipboard import ClipboardTool
    from knoa_platform.tools.hotkey import HotkeyTool
    from knoa_platform.tools.mouse import MouseTool
    from knoa_platform.tools.notification import NotificationTool
    from knoa_platform.tools.press_key import PressKeyTool
    from knoa_platform.tools.type_text import TypeTextTool
    from knoa_platform.tools.window import WindowTool

    tool_types = {
        "clipboard": ClipboardTool,
        "hotkey": HotkeyTool,
        "mouse": MouseTool,
        "notify": NotificationTool,
        "press_key": PressKeyTool,
        "type_text": TypeTextTool,
        "windows": WindowTool,
    }
    tool_type = tool_types.get(tool_name)
    if tool_type is None:
        raise DesktopCompanionError("Desktop tool is not implemented by the Companion")
    result = await tool_type().execute(**arguments)
    if not isinstance(result, dict):
        raise DesktopCompanionError("Desktop tool returned a non-object result")
    return result


def _serve_connection(connection: Any, session_id: int) -> None:
    try:
        raw = connection.recv_bytes(_MAX_REQUEST_BYTES)
        request = json.loads(raw)
        if not isinstance(request, dict) or request.get("version") != 1:
            raise DesktopCompanionError("Unsupported Desktop Companion protocol")
        if request.get("session_id") != session_id:
            raise DesktopCompanionError("Desktop request targets another Windows session")
        tool_name = request.get("tool")
        arguments = request.get("arguments")
        if tool_name not in _ALLOWED_TOOLS or not isinstance(arguments, dict):
            raise DesktopCompanionError("Invalid Desktop Companion request")
        result = asyncio.run(_execute_tool(str(tool_name), arguments))
        response = {"result": result}
    except Exception as exc:  # noqa: BLE001 - isolate one untrusted local request
        response = {"error": str(exc)[:500] or type(exc).__name__}
    encoded = _json_bytes(response)
    if len(encoded) > _MAX_RESPONSE_BYTES:
        encoded = _json_bytes({"error": "Desktop Companion response is too large"})
    connection.send_bytes(encoded)


def serve_desktop_companion(token_file: str | Path) -> None:
    if os.name != "nt":
        raise DesktopCompanionError("Desktop Companion is only supported on Windows")
    session_id = _windows_process_session_id()
    if session_id <= 0:
        raise DesktopCompanionError("Desktop Companion must run in an interactive user session")
    os.environ[_COMPANION_PROCESS_ENV] = "1"
    listener = Listener(
        _pipe_address(session_id),
        family="AF_PIPE",
        authkey=_read_authkey(token_file),
    )
    try:
        while True:
            connection = listener.accept()
            try:
                _serve_connection(connection, session_id)
            finally:
                connection.close()
    finally:
        listener.close()


def main() -> int:
    parser = argparse.ArgumentParser(prog="knoa-desktop-companion")
    parser.add_argument("--token-file", type=Path, required=True)
    args = parser.parse_args()
    serve_desktop_companion(args.token_file)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "DesktopCompanionError",
    "companion_available",
    "desktop_companion_required",
    "desktop_companion_status",
    "invoke_desktop_companion",
    "serve_desktop_companion",
]
