"""System clipboard helpers for the chat TUI.

Textual paints its own screen, so the terminal's native copy is unreliable in
alternate-screen mode. These helpers write real text to the OS clipboard
directly via the platform tool (wl-copy / xclip / xsel / pbcopy / clip.exe),
which works regardless of whether the terminal selection copied anything.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys

_COPY_TIMEOUT = 5


def _run(command: list[str], data: bytes) -> bool:
    try:
        if os.name == "nt":
            proc = subprocess.run(command, input=data, capture_output=True, timeout=_COPY_TIMEOUT)
            return proc.returncode == 0
        # POSIX clipboard tools (xclip/xsel) daemonize as the X selection owner
        # and keep inherited stdout/stderr open, which makes subprocess.run hang.
        # Popen with DEVNULL + start_new_session + communicate lets the parent
        # return immediately while the child owns the clipboard.
        proc = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        proc.communicate(data, timeout=_COPY_TIMEOUT)
        return proc.returncode == 0
    except Exception:
        return False


def available_tool() -> str:
    """Return the name of the clipboard tool that would be used, or ''."""
    if sys.platform == "darwin" and shutil.which("pbcopy"):
        return "pbcopy"
    if os.name == "nt" and shutil.which("clip"):
        return "clip"
    for tool in ("wl-copy", "xclip", "xsel"):
        if shutil.which(tool):
            return tool
    return ""


async def copy_to_clipboard(text: str) -> tuple[bool, str]:
    """Copy ``text`` to the system clipboard.

    Returns ``(ok, detail)`` where detail names the tool used or the reason for
    failure, so the caller can fall back (e.g. save to a file).
    """
    if not text:
        return False, "Nothing to copy"

    data = text.encode("utf-8")

    if sys.platform == "darwin" and shutil.which("pbcopy"):
        return _run(["pbcopy"], data), "copied via pbcopy"

    if os.name == "nt" and shutil.which("clip"):
        # clip.exe expects UTF-16LE with a BOM.
        utf16 = text.encode("utf-16-le")
        return _run(["clip"], utf16), "copied via clip.exe"

    # Linux: Wayland first, then X11.
    if shutil.which("wl-copy"):
        return _run(["wl-copy"], data), "copied via wl-copy"
    if shutil.which("xclip"):
        return _run(["xclip", "-selection", "clipboard"], data), "copied via xclip"
    if shutil.which("xsel"):
        return _run(["xsel", "--clipboard", "--input"], data), "copied via xsel"

    return False, "No clipboard tool found (install xclip, wl-clipboard or pbcopy)."


async def copy_or_save(text: str, fallback_path: str = "") -> tuple[bool, str]:
    """Copy to the clipboard; if unavailable, write to a file instead.

    Returns ``(ok, detail)`` — ``ok`` is True when the text reached somewhere
    the user can retrieve it.
    """
    ok, detail = await copy_to_clipboard(text)
    if ok:
        return True, detail
    try:
        if not fallback_path:
            from pc_assistant.runtime import RuntimePaths
            fallback_path = str(RuntimePaths.from_root().cache / "clipboard.txt")
        os.makedirs(os.path.dirname(os.path.abspath(fallback_path)), exist_ok=True)
        with open(fallback_path, "w", encoding="utf-8") as fh:
            fh.write(text)
        return True, f"clipboard unavailable ({detail}); saved to {fallback_path}"
    except OSError as e:
        return False, f"failed to save fallback file: {e}"
