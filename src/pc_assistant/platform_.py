from __future__ import annotations

import platform


_WINDOWS_DANGEROUS = [
    "del /s /q",
    "rd /s",
    "rmdir /s",
    "remove-item -recurse",
    "format",
    "shutdown",
    "taskkill /f",
    "reg delete",
    "net user",
    "cipher /w",
    "diskpart",
    "bcdedit",
]

_LINUX_DANGEROUS = [
    "rm -rf /",
    "mkfs",
    "dd if=",
    "shutdown",
    "reboot",
    "fdisk",
    "mkswap",
    "systemctl stop",
    "init 0",
    "init 6",
    ":(){ :|:& };:",
]

_MACOS_DANGEROUS = [
    "rm -rf /",
    "diskutil eraseDisk",
    "diskutil partitionDisk",
    "shutdown",
    "mkfs",
    "dd if=",
]

_WINDOWS_PROTECTED = [
    "C:\\Windows\\System32",
    "C:\\Windows\\SysWOW64",
    "C:\\Program Files",
    "C:\\ProgramData",
]

_LINUX_PROTECTED = [
    "/etc/passwd",
    "/etc/shadow",
    "/etc/sudoers",
    "/boot",
    "/proc",
    "/sys",
]

_MACOS_PROTECTED = [
    "/System",
    "/Library",
    "/usr/sbin",
    "/private/var/db",
]


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


def get_default_dangerous_commands() -> list[str]:
    plat = get_platform()
    if plat == "windows":
        return list(_WINDOWS_DANGEROUS)
    if plat == "linux":
        return list(_LINUX_DANGEROUS)
    return list(_MACOS_DANGEROUS)


def get_default_protected_paths() -> list[str]:
    plat = get_platform()
    if plat == "windows":
        return list(_WINDOWS_PROTECTED)
    if plat == "linux":
        return list(_LINUX_PROTECTED)
    return list(_MACOS_PROTECTED)


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
