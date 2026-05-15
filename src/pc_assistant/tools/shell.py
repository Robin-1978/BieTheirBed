from __future__ import annotations

import asyncio
import re
import shlex
import subprocess
from typing import Any

from pc_assistant.platform_ import get_platform
from pc_assistant.tools.base import ToolBase


_DEFAULT_TIMEOUT = 30


class UacExecutor:
    """Windows UAC executor using ShellExecute with 'runas'."""

    @staticmethod
    def execute(command: str, timeout: int | None = None) -> dict[str, Any]:
        """Execute command with elevated privileges on Windows.

        Uses ShellExecuteW with 'runas' to trigger UAC dialog.
        """
        import ctypes
        from ctypes import wintypes

        SW_SHOW = 5
        SEE_MASK_NOCLOSEPROCESS = 0x00000040

        class SHELLEXECUTEINFO(ctypes.Structure):
            _fields_ = [
                ("cbSize", wintypes.DWORD),
                ("fMask", wintypes.DWORD),
                ("hwnd", wintypes.HWND),
                ("lpVerb", wintypes.LPCWSTR),
                ("lpFile", wintypes.LPCWSTR),
                ("lpParameters", wintypes.LPCWSTR),
                ("lpDirectory", wintypes.LPCWSTR),
                ("nShow", ctypes.c_int),
                ("hInstApp", wintypes.HINSTANCE),
                ("lpIDList", ctypes.c_void_p),
                ("lpClass", wintypes.LPCWSTR),
                ("hkeyClass", wintypes.HKEY),
                ("dwHotKey", wintypes.DWORD),
                ("hIcon", wintypes.HANDLE),
                ("hProcess", wintypes.HANDLE),
            ]

        shell32 = ctypes.windll.shell32
        ShellExecuteExW = shell32.ShellExecuteExW
        ShellExecuteExW.argtypes = [ctypes.POINTER(SHELLEXECUTEINFO)]
        ShellExecuteExW.restype = wintypes.BOOL

        sei = SHELLEXECUTEINFO()
        sei.cbSize = ctypes.sizeof(SHELLEXECUTEINFO)
        sei.fMask = SEE_MASK_NOCLOSEPROCESS
        sei.lpVerb = "runas"
        sei.lpFile = "cmd.exe"
        sei.lpParameters = f"/c {command}"
        sei.nShow = SW_SHOW

        if ShellExecuteExW(ctypes.byref(sei)):
            if sei.hProcess:
                try:
                    if timeout:
                        retcode = ctypes.windll.kernel32.WaitForSingleObject(sei.hProcess, timeout * 1000)
                        if retcode == 0x00000102:  # WAIT_TIMEOUT
                            ctypes.windll.kernel32.TerminateProcess(sei.hProcess, -1)
                            return {"error": f"Command timed out after {timeout}s", "returncode": -1}
                    else:
                        ctypes.windll.kernel32.WaitForSingleObject(sei.hProcess, -1)
                    retcode = ctypes.windll.kernel32.GetExitCodeProcess(sei.hProcess)
                    ctypes.windll.kernel32.CloseHandle(sei.hProcess)
                    return {"returncode": retcode, "stdout": "", "stderr": "", "success": retcode == 0}
                except Exception as e:
                    return {"error": str(e), "returncode": -1}
            return {"returncode": 0, "stdout": "", "stderr": "", "success": True}
        else:
            error = ctypes.windll.kernel32.GetLastError()
            return {"error": f"UAC dialog cancelled or failed (error code: {error})", "returncode": -1}


class MacOsAuthorizationExecutor:
    """macOS authorization executor using Authorization Services."""

    @staticmethod
    def execute(command: str, timeout: int | None = None) -> dict[str, Any]:
        """Execute command with elevated privileges on macOS.

        Uses AppleScript with administrator privileges to trigger authentication dialog.
        """
        # Escape command for AppleScript
        escaped_cmd = command.replace('"', '\\"')

        script = f'''
do shell script "{escaped_cmd}" with administrator privileges
'''
        try:
            result = subprocess.run(
                ["osascript", "-e", script],
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            return {
                "returncode": result.returncode,
                "stdout": result.stdout,
                "stderr": result.stderr,
                "success": result.returncode == 0,
            }
        except subprocess.TimeoutExpired:
            return {"error": f"Command timed out after {timeout}s", "returncode": -1}
        except Exception as e:
            return {"error": str(e), "returncode": -1}


class ShellTool(ToolBase):
    name = "shell"
    description = "Execute shell commands with full shell support (pipes, redirects, etc.)"

    async def execute(self, **kwargs: Any) -> Any:
        command = kwargs.get("command", "")
        timeout = kwargs.get("timeout")
        cwd = kwargs.get("cwd")
        env = kwargs.get("env")
        if not command:
            return {"error": "No command provided"}
        try:
            timeout_val = int(timeout) if timeout is not None else _DEFAULT_TIMEOUT
        except (ValueError, TypeError):
            timeout_val = _DEFAULT_TIMEOUT
        return await self._run(command, timeout_val, cwd, env)

    def schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "Shell command to execute (supports pipes, redirects, etc.)"
                    },
                    "timeout": {"type": "integer", "description": "Timeout in seconds (default: 30)"},
                    "cwd": {"type": "string", "description": "Working directory for command"},
                    "env": {
                        "type": "object",
                        "description": "Environment variables to set for this command"
                    },
                },
                "required": ["command"],
            },
        }

    def _needs_privilege(self, command: str) -> bool:
        """Check if command requires privilege escalation."""
        return bool(re.search(r'^\s*sudo\s+', command)) and "-n" not in command

    def _convert_sudo_command(self, command: str) -> str:
        """Remove sudo prefix since we'll use platform-specific executor."""
        return re.sub(r'^\s*sudo\s+', '', command, count=1).strip()

    async def _run(
        self,
        command: str,
        timeout: int | None,
        cwd: str | None,
        env: dict[str, str] | None,
    ) -> dict[str, Any]:
        import os
        plat = get_platform()

        # Build environment
        full_env = None
        if env:
            full_env = {**os.environ, **env}

        # Check if command needs privilege escalation
        needs_privilege = self._needs_privilege(command)

        # Platform-specific privilege escalation
        if needs_privilege:
            privileged_cmd = self._convert_sudo_command(command)

            if plat == "windows":
                # Use Windows UAC
                result = UacExecutor.execute(privileged_cmd, timeout)
                result["command"] = f"sudo {privileged_cmd}"
                return result

            elif plat == "darwin":
                # Use macOS Authorization
                result = MacOsAuthorizationExecutor.execute(privileged_cmd, timeout)
                result["command"] = f"sudo {privileged_cmd}"
                return result

            else:
                # Linux: use pkexec
                privileged_cmd = command.replace("sudo ", "pkexec ", 1)
                return await self._execute_simple(privileged_cmd, timeout, cwd, full_env, command)

        # Normal execution without privilege escalation
        return await self._execute_simple(command, timeout, cwd, full_env, command)

    async def _execute_simple(
        self,
        command: str,
        timeout: int | None,
        cwd: str | None,
        env: dict[str, str] | None,
        original_command: str,
    ) -> dict[str, Any]:
        """Execute command without privilege escalation."""
        plat = get_platform()

        try:
            if plat == "windows":
                shell_exe = "cmd.exe"
                shell_args = ["/c", command]
            else:
                shell_exe = "/bin/bash"
                shell_args = ["-c", command]

            proc = await asyncio.create_subprocess_exec(
                shell_exe, *shell_args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
                env=env,
            )

            try:
                stdout_bytes, stderr_bytes = await asyncio.wait_for(
                    proc.communicate(), timeout=timeout
                )
            except asyncio.TimeoutError:
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass
                await proc.communicate()
                return {
                    "error": f"Command timed out after {timeout}s",
                    "returncode": -1,
                    "command": original_command,
                }

            stdout = stdout_bytes.decode("utf-8", errors="replace")
            stderr = stderr_bytes.decode("utf-8", errors="replace")
            return {
                "returncode": proc.returncode,
                "stdout": stdout,
                "stderr": stderr,
                "command": original_command,
                "success": proc.returncode == 0,
            }
        except FileNotFoundError:
            return {
                "error": f"Shell not found: {shell_exe}",
                "returncode": -1,
                "command": original_command,
            }
        except PermissionError:
            return {
                "error": f"Permission denied: {shell_exe}",
                "returncode": -1,
                "command": original_command,
            }
        except Exception as e:
            return {"error": str(e), "returncode": -1, "command": original_command}
