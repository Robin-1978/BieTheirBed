"""Privileged, loopback-only Host lifecycle broker shared by Hub and Node Consoles."""

from __future__ import annotations

import argparse
import hmac
import json
import os
import platform
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Literal

import uvicorn
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from knoa_platform.network_tls import is_loopback_host

HostRole = Literal["hub", "node"]


class _Action(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    action: Literal["restart", "activate", "deactivate", "rollback", "update"]
    role: HostRole | None = None
    bundle_name: str | None = Field(default=None, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,255}$")


class HostLifecycleManager:
    """Owns only fixed Knoa services and signed product releases."""

    def __init__(
        self,
        *,
        updater: Path,
        release_root: Path,
        trust_store: Path,
        state_file: Path,
        incoming_root: Path,
    ) -> None:
        self.updater = updater.resolve()
        self.release_root = release_root.resolve()
        self.trust_store = trust_store.resolve()
        self.state_file = state_file.resolve()
        self.incoming_root = incoming_root.resolve()
        self.incoming_root.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _service_name(role: HostRole) -> str:
        if os.name == "nt":
            return "KnoaHostedHub" if role == "hub" else "KnoaNode"
        return "knoa-hub.service" if role == "hub" else "knoa-node.service"

    def _run(self, command: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            command,
            check=check,
            capture_output=True,
            text=True,
            timeout=300,
        )

    def _roles(self) -> set[HostRole]:
        if not self.state_file.is_file():
            return set()
        document = json.loads(self.state_file.read_text(encoding="utf-8"))
        values = document.get("installed_roles", [])
        return {value for value in values if value in {"hub", "node"}}

    def _write_roles(self, roles: set[HostRole]) -> None:
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(
            {"schema_version": 1, "installed_roles": sorted(roles)},
            ensure_ascii=False,
            indent=2,
        ) + "\n"
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=self.state_file.parent,
            prefix=f".{self.state_file.name}.",
            suffix=".tmp",
            delete=False,
        ) as stream:
            stream.write(payload)
            temporary = Path(stream.name)
        os.replace(temporary, self.state_file)

    def _service_active(self, role: HostRole) -> bool:
        name = self._service_name(role)
        if os.name == "nt":
            result = self._run(["sc.exe", "query", name], check=False)
            return result.returncode == 0 and "RUNNING" in result.stdout
        return self._run(["systemctl", "is-active", "--quiet", name], check=False).returncode == 0

    def _service(self, role: HostRole, action: Literal["start", "stop", "restart", "enable", "disable"]) -> None:
        name = self._service_name(role)
        if os.name == "nt":
            commands = {
                "start": ["sc.exe", "start", name],
                "stop": ["sc.exe", "stop", name],
                "restart": ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", f"Restart-Service -Name '{name}' -Force"],
                "enable": ["sc.exe", "config", name, "start=", "auto"],
                "disable": ["sc.exe", "config", name, "start=", "disabled"],
            }
        else:
            commands = {
                "start": ["systemctl", "start", name],
                "stop": ["systemctl", "stop", name],
                "restart": ["systemctl", "restart", name],
                "enable": ["systemctl", "enable", name],
                "disable": ["systemctl", "disable", name],
            }
        self._run(commands[action])

    def _current_release(self) -> str | None:
        result = self._run(
            [str(self.updater), "current", "--install-root", str(self.release_root)],
            check=False,
        )
        return result.stdout.strip() or None if result.returncode == 0 else None

    @staticmethod
    def _health_url(role: HostRole) -> str:
        return "http://127.0.0.1:9529/health" if role == "hub" else "http://127.0.0.1:9531/health"

    def _wait_healthy(self, roles: tuple[HostRole, ...], timeout_seconds: float = 60.0) -> None:
        for role in roles:
            deadline = time.monotonic() + timeout_seconds
            while True:
                try:
                    with urllib.request.urlopen(self._health_url(role), timeout=3) as response:
                        if response.status == 200:
                            break
                except (OSError, urllib.error.URLError):
                    pass
                if time.monotonic() >= deadline:
                    raise RuntimeError(f"{role}_health_failed")
                time.sleep(0.5)

    def status(self) -> dict[str, object]:
        roles = self._roles()
        return {
            "platform": "windows" if os.name == "nt" else "linux",
            "architecture": platform.machine().lower(),
            "current_release": self._current_release(),
            "installed_roles": sorted(roles),
            "services": {
                role: {
                    "installed": role in roles,
                    "active": self._service_active(role),
                }
                for role in ("hub", "node")
            },
        }

    def act(self, request: _Action) -> dict[str, object]:
        roles = self._roles()
        if request.action in {"restart", "activate", "deactivate"} and request.role is None:
            raise ValueError("role_required")
        if request.action == "restart":
            assert request.role is not None
            if request.role not in roles:
                raise ValueError("role_not_installed")
            self._service(request.role, "restart")
        elif request.action == "activate":
            assert request.role is not None
            self._service(request.role, "enable")
            try:
                self._service(request.role, "start")
                self._wait_healthy((request.role,))
            except Exception:
                self._service(request.role, "stop")
                self._service(request.role, "disable")
                raise
            roles.add(request.role)
            self._write_roles(roles)
        elif request.action == "deactivate":
            assert request.role is not None
            self._service(request.role, "stop")
            self._service(request.role, "disable")
            roles.discard(request.role)
            self._write_roles(roles)
        elif request.action == "rollback":
            active = tuple(role for role in ("hub", "node") if role in roles)
            for role in active:
                self._service(role, "stop")
            try:
                self._run([
                    str(self.updater), "rollback",
                    "--install-root", str(self.release_root),
                    "--health-entrypoint", "bin/knoa-health" + (".cmd" if os.name == "nt" else ""),
                ])
            finally:
                for role in active:
                    self._service(role, "start")
            self._wait_healthy(active)
        elif request.action == "update":
            if request.bundle_name is None:
                raise ValueError("bundle_required")
            bundle = (self.incoming_root / request.bundle_name).resolve()
            if bundle.parent != self.incoming_root or not bundle.is_file():
                raise ValueError("bundle_not_found")
            active = tuple(role for role in ("hub", "node") if role in roles)
            for role in active:
                self._service(role, "stop")
            target_arch = "aarch64" if platform.machine().lower() in {"arm64", "aarch64"} else "x86_64"
            staging = self.release_root.parent / ".incoming-lifecycle"
            try:
                self._run([
                    str(self.updater), "install",
                    "--archive", str(bundle),
                    "--staging", str(staging),
                    "--trust-store", str(self.trust_store),
                    "--kind", "product", "--role", "all",
                    "--target-os", "windows" if os.name == "nt" else "linux",
                    "--target-arch", target_arch,
                    "--install-root", str(self.release_root),
                    "--health-entrypoint", "bin/knoa-health" + (".cmd" if os.name == "nt" else ""),
                ])
            except Exception:
                for role in active:
                    self._service(role, "start")
                raise
            for role in active:
                self._service(role, "start")
            try:
                self._wait_healthy(active)
            except RuntimeError:
                for role in active:
                    self._service(role, "stop")
                self._run([
                    str(self.updater), "reject",
                    "--install-root", str(self.release_root),
                    "--health-entrypoint", "bin/knoa-health" + (".cmd" if os.name == "nt" else ""),
                ])
                for role in active:
                    self._service(role, "start")
                self._wait_healthy(active)
                raise
            bundle.unlink(missing_ok=True)
        return self.status()


def create_lifecycle_app(manager: HostLifecycleManager, token: str) -> Starlette:
    if len(token) < 32:
        raise ValueError("Host lifecycle token must contain at least 32 characters")

    def authorized(request: Request) -> bool:
        supplied = request.headers.get("Authorization", "")
        return hmac.compare_digest(supplied, f"Bearer {token}")

    async def status(request: Request) -> JSONResponse:
        if not authorized(request):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        return JSONResponse(manager.status(), headers={"Cache-Control": "no-store"})

    async def action(request: Request) -> JSONResponse:
        if not authorized(request):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        try:
            parsed = _Action.model_validate_json(await request.body())
            result = manager.act(parsed)
        except ValidationError:
            return JSONResponse({"error": "invalid_action"}, status_code=400)
        except ValueError as error:
            return JSONResponse({"error": str(error)}, status_code=409)
        except RuntimeError as error:
            return JSONResponse({"error": str(error)}, status_code=503)
        except (OSError, subprocess.SubprocessError):
            return JSONResponse({"error": "lifecycle_action_failed"}, status_code=503)
        return JSONResponse(result, headers={"Cache-Control": "no-store"})

    return Starlette(routes=[
        Route("/v1/lifecycle", status, methods=["GET"]),
        Route("/v1/lifecycle/actions", action, methods=["POST"]),
    ])


def main() -> int:
    parser = argparse.ArgumentParser(prog="knoa-host-lifecycle")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9533)
    parser.add_argument("--updater", type=Path, required=True)
    parser.add_argument("--release-root", type=Path, required=True)
    parser.add_argument("--trust-store", type=Path, required=True)
    parser.add_argument("--state-file", type=Path, required=True)
    parser.add_argument("--incoming-root", type=Path, required=True)
    parser.add_argument("--token-file", type=Path, required=True)
    args = parser.parse_args()
    if not is_loopback_host(args.host):
        parser.error("Host lifecycle broker must bind to loopback")
    token = args.token_file.read_text(encoding="utf-8").strip()
    manager = HostLifecycleManager(
        updater=args.updater,
        release_root=args.release_root,
        trust_store=args.trust_store,
        state_file=args.state_file,
        incoming_root=args.incoming_root,
    )
    uvicorn.run(create_lifecycle_app(manager, token), host=args.host, port=args.port, access_log=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["HostLifecycleManager", "create_lifecycle_app"]
