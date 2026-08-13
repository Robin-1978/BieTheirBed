from __future__ import annotations

import platform


def get_platform() -> str:
    system = platform.system()
    if system == "Windows":
        return "windows"
    if system == "Linux":
        return "linux"
    if system == "Darwin":
        return "macos"
    raise RuntimeError(f"Unsupported platform: {system}")


def get_shell_command() -> tuple[str, str]:
    plat = get_platform()
    if plat == "windows":
        return ("powershell", "-Command")
    if plat == "linux":
        return ("/bin/bash", "-c")
    return ("/bin/zsh", "-c")


def get_shell_name() -> str:
    plat = get_platform()
    if plat == "windows":
        return "PowerShell"
    if plat == "linux":
        return "bash"
    return "zsh"


def get_path_separator() -> str:
    plat = get_platform()
    if plat == "windows":
        return "\\"
    return "/"


def normalize_path(path: str) -> str:
    sep = get_path_separator()
    if sep == "\\":
        return path.replace("/", "\\")
    return path.replace("\\", "/")
