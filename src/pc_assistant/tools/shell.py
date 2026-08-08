from __future__ import annotations

import asyncio
import os
import re
import signal
import subprocess
from typing import Any

from pc_assistant.platform_ import get_platform
from pc_assistant.tools.base import ToolBase, ToolCapability, ToolEffect, ToolRisk


_DEFAULT_TIMEOUT = 30
_MAX_OUTPUT_BYTES = 1024 * 1024


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
    name = "run_command"
    description = "Execute a shell command, return stdout/stderr."
    effect = ToolEffect.LOCAL_WRITE
    capabilities = frozenset(
        {
            ToolCapability.SHELL,
            ToolCapability.HOST_READ,
            ToolCapability.HOST_WRITE,
            ToolCapability.NETWORK,
        }
    )
    risk = ToolRisk.HIGH

    def __init__(self, default_timeout: int = 30) -> None:
        self._default_timeout = default_timeout

    async def execute(self, **kwargs: Any) -> Any:
        command = kwargs.get("command", "")
        timeout = kwargs.get("timeout_seconds")
        cwd = kwargs.get("working_directory")
        env = kwargs.get("environment")
        if not command:
            return {"error": "No command provided"}
        try:
            timeout_val = int(timeout) if timeout is not None else self._default_timeout
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
                        "description": "Shell command to execute (supports pipes, redirects, etc.)",
                    },
                    "timeout_seconds": {
                        "type": "integer",
                        "description": "Timeout in seconds (default: 30)",
                    },
                    "working_directory": {
                        "type": "string",
                        "description": "Working directory for command",
                    },
                    "environment": {
                        "type": "object",
                        "description": "Environment variables to set for this command",
                    },
                },
                "required": ["command"],
            },
        }

    def skim_schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "shell command; may require confirmation"},
                    "timeout_seconds": {"type": "integer", "description": "default 30"},
                    "working_directory": {"type": "string"},
                    "environment": {"type": "object", "description": "extra environment variables"},
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
        working_directory: str | None,
        environment: dict[str, str] | None,
    ) -> dict[str, Any]:
        import os
        plat = get_platform()

        # Build environment
        full_env = None
        if environment:
            full_env = {**os.environ, **environment}

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
                return await self._execute_simple(privileged_cmd, timeout, working_directory, full_env, command)

        # Normal execution without privilege escalation
        return await self._execute_simple(command, timeout, working_directory, full_env, command)

    async def _execute_simple(
        self,
        command: str,
        timeout: int | None,
        working_directory: str | None,
        environment: dict[str, str] | None,
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

            # Put each command in its own process group.  A shell command may
            # spawn descendants (for example ``find``); killing only bash on
            # timeout leaves those descendants holding our pipes open and can
            # make the whole agent turn hang until the channel times out.
            isolated_process_group = plat != "windows"
            process_kwargs: dict[str, Any] = {
                "stdout": asyncio.subprocess.PIPE,
                "stderr": asyncio.subprocess.PIPE,
                "cwd": working_directory,
                "env": environment,
            }
            if isolated_process_group:
                process_kwargs["start_new_session"] = True
            proc = await asyncio.create_subprocess_exec(
                shell_exe, *shell_args,
                **process_kwargs,
            )

            output_limit_reached = asyncio.Event()
            stdout_buffer = bytearray()
            stderr_buffer = bytearray()

            async def read_limited(stream, buffer: bytearray) -> None:
                discarding = False
                while True:
                    chunk = await stream.read(64 * 1024)
                    if not chunk:
                        return
                    if discarding:
                        continue
                    remaining = _MAX_OUTPUT_BYTES - len(buffer)
                    if remaining <= 0:
                        output_limit_reached.set()
                        discarding = True
                        continue
                    buffer.extend(chunk[:remaining])
                    if len(chunk) > remaining:
                        output_limit_reached.set()
                        discarding = True

            def terminate_process_group() -> None:
                if isolated_process_group:
                    try:
                        os.killpg(proc.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                else:
                    try:
                        proc.kill()
                    except ProcessLookupError:
                        pass

            stdout_task = asyncio.create_task(
                read_limited(proc.stdout, stdout_buffer)
            )
            stderr_task = asyncio.create_task(
                read_limited(proc.stderr, stderr_buffer)
            )
            process_task = asyncio.create_task(proc.wait())
            limit_task = asyncio.create_task(output_limit_reached.wait())
            timed_out = False
            output_exceeded = False
            try:
                done, _pending = await asyncio.wait_for(
                    asyncio.wait(
                        {process_task, limit_task},
                        return_when=asyncio.FIRST_COMPLETED,
                    ),
                    timeout=timeout,
                )
                output_exceeded = (
                    limit_task in done and output_limit_reached.is_set()
                )
                if output_exceeded:
                    terminate_process_group()
                await process_task
            except asyncio.TimeoutError:
                timed_out = True
                terminate_process_group()
                await process_task
            except asyncio.CancelledError:
                terminate_process_group()
                await process_task
                raise
            except Exception:
                terminate_process_group()
                await process_task
                raise
            finally:
                limit_task.cancel()
                await asyncio.gather(limit_task, return_exceptions=True)
                await asyncio.gather(
                    stdout_task,
                    stderr_task,
                    return_exceptions=True,
                )

            stdout = bytes(stdout_buffer).decode("utf-8", errors="replace")
            stderr = bytes(stderr_buffer).decode("utf-8", errors="replace")
            if timed_out:
                return {
                    "error": f"Command timed out after {timeout}s",
                    "returncode": -1,
                    "command": original_command,
                }
            if output_exceeded:
                return {
                    "error": f"Command output exceeded {_MAX_OUTPUT_BYTES} bytes",
                    "returncode": -1,
                    "stdout": stdout,
                    "stderr": stderr,
                    "output_truncated": True,
                    "command": original_command,
                }
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
