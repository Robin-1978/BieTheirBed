"""Cross-platform Git-backed Source Release Channel.

The mutable checkout is only a channel input.  Installations are always made
from immutable detached worktrees so a failed update can reinstall the prior
commit without rewriting the user's checkout.
"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


class SourceUpdateError(RuntimeError):
    """Stable failure returned by the lifecycle boundary."""


class SourceUpdateManager:
    def __init__(
        self,
        *,
        source_root: Path,
        state_file: Path,
        snapshots_root: Path,
        installation_state_file: Path,
    ) -> None:
        self.source_root = source_root.resolve()
        self.state_file = state_file.resolve()
        self.snapshots_root = snapshots_root.resolve()
        self.installation_state_file = installation_state_file.resolve()

    def _run(
        self,
        command: list[str],
        *,
        cwd: Path | None = None,
        check: bool = True,
        timeout: float = 1800,
        environment: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run(
                command,
                cwd=cwd,
                check=check,
                capture_output=True,
                text=True,
                timeout=timeout,
                env=environment,
            )
        except FileNotFoundError as exc:
            raise SourceUpdateError("source_update_dependency_missing") from exc
        except subprocess.TimeoutExpired as exc:
            raise SourceUpdateError("source_update_timeout") from exc
        except subprocess.CalledProcessError as exc:
            detail = (exc.stderr or exc.stdout or "source update command failed").strip()
            raise SourceUpdateError(detail[:500]) from exc

    def _git(self, *arguments: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        return self._run(
            ["git", "-c", f"safe.directory={self.source_root}", *arguments],
            cwd=self.source_root,
            check=check,
            timeout=300,
        )

    def _read_state(self) -> dict[str, Any]:
        if not self.state_file.is_file():
            return {
                "schema_version": 1,
                "current_commit": "",
                "last_checked_commit": "",
            }
        try:
            state = json.loads(self.state_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise SourceUpdateError("source_update_state_invalid") from exc
        if state.get("schema_version") != 1:
            raise SourceUpdateError("source_update_state_invalid")
        return state

    def _write_state(self, state: dict[str, Any]) -> None:
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(state, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
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

    def _installation(self) -> dict[str, Any]:
        try:
            document = json.loads(self.installation_state_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise SourceUpdateError("source_installation_state_invalid") from exc
        if document.get("schema_version") != 1:
            raise SourceUpdateError("source_installation_state_invalid")
        role = document.get("role")
        if role not in {"hub", "node", "all"}:
            raise SourceUpdateError("source_installation_role_invalid")
        return document

    def _head(self, revision: str = "HEAD") -> str:
        value = self._git("rev-parse", "--verify", revision).stdout.strip().lower()
        if len(value) != 40 or any(character not in "0123456789abcdef" for character in value):
            raise SourceUpdateError("source_revision_invalid")
        return value

    def _upstream(self) -> str:
        upstream = self._git(
            "rev-parse",
            "--abbrev-ref",
            "--symbolic-full-name",
            "@{upstream}",
        ).stdout.strip()
        if not upstream or upstream.startswith("-") or any(character.isspace() for character in upstream):
            raise SourceUpdateError("source_upstream_invalid")
        return upstream

    def _assert_clean(self) -> None:
        changes = self._git("status", "--porcelain", "--untracked-files=no").stdout.strip()
        if changes:
            raise SourceUpdateError("source_checkout_has_tracked_changes")

    def _fetch_latest(self) -> tuple[str, str]:
        self._assert_clean()
        upstream = self._upstream()
        remote = upstream.split("/", 1)[0]
        self._git("fetch", "--prune", remote)
        latest = self._head("@{upstream}")
        return upstream, latest

    @staticmethod
    def _valid_commit(value: object) -> str:
        commit = str(value or "").lower()
        if len(commit) == 40 and all(character in "0123456789abcdef" for character in commit):
            return commit
        return ""

    def _installed_commit(self, state: dict[str, Any]) -> str:
        installed = self._valid_commit(self._installation().get("installed_commit"))
        if installed:
            return installed
        current = str(state.get("current_commit") or "").lower()
        if self._valid_commit(current):
            return current
        # Migration fallback for source installations created before
        # installation.json recorded the immutable installed revision.
        return self._head()

    def _assert_fast_forward(self, current: str, latest: str) -> None:
        result = self._git("merge-base", "--is-ancestor", current, latest, check=False)
        if result.returncode != 0:
            raise SourceUpdateError("source_history_diverged")

    def _snapshot(self, commit: str) -> Path:
        self.snapshots_root.mkdir(parents=True, exist_ok=True)
        destination = self.snapshots_root / commit
        if destination.is_dir():
            actual = self._run(
                ["git", "rev-parse", "--verify", "HEAD"],
                cwd=destination,
                timeout=30,
            ).stdout.strip().lower()
            if actual != commit:
                raise SourceUpdateError("source_snapshot_invalid")
            return destination
        self._git("worktree", "add", "--detach", str(destination), commit)
        return destination

    def _discard_snapshot(self, snapshot: Path) -> None:
        try:
            self._git("worktree", "remove", "--force", str(snapshot), check=False)
            self._git("worktree", "prune", check=False)
        except SourceUpdateError:
            # Cleanup is best-effort. A stale staging worktree is safe and can
            # be diagnosed or reused by the next update.
            pass

    @staticmethod
    def _health_urls(role: str) -> tuple[str, ...]:
        values: list[str] = []
        if role in {"hub", "all"}:
            values.append("http://127.0.0.1:9529/health")
        if role in {"node", "all"}:
            values.append("http://127.0.0.1:9531/health")
        return tuple(values)

    def _wait_healthy(self, role: str, timeout_seconds: float = 90.0) -> None:
        for url in self._health_urls(role):
            deadline = time.monotonic() + timeout_seconds
            while True:
                try:
                    with urllib.request.urlopen(url, timeout=3) as response:
                        if response.status == 200:
                            break
                except (OSError, urllib.error.URLError):
                    pass
                if time.monotonic() >= deadline:
                    raise SourceUpdateError("source_update_health_failed")
                time.sleep(0.5)

    def _install(self, snapshot: Path, installation: dict[str, Any]) -> None:
        role = str(installation["role"])
        environment = dict(os.environ)
        environment["KNOA_SOURCE_UPDATE_ACTIVE"] = "1"
        if os.name == "nt":
            script = snapshot / "deploy" / "windows" / "Install-Knoa.ps1"
            command = [
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(script),
                "-Role",
                role,
                "-SourcePath",
                str(snapshot),
                "-ChannelSourcePath",
                str(self.source_root),
                "-HubPublicUrl",
                str(installation.get("hub_public_url") or "https://knoa.tinydotdot.com"),
                "-SkipPairingQr",
            ]
            optional = {
                "HubRoot": "hub_root",
                "NodeRoot": "node_root",
                "InstallRoot": "install_root",
                "HubId": "hub_id",
                "PythonVersion": "python_version",
                "HubPort": "hub_port",
                "NodeCorePort": "node_core_port",
                "NodeGatewayPort": "node_gateway_port",
                "NodeMcpPort": "node_mcp_port",
            }
            for argument, key in optional.items():
                value = installation.get(key)
                if value not in (None, ""):
                    command.extend((f"-{argument}", str(value)))
        else:
            script = snapshot / "deploy" / "linux" / "install-knoa.sh"
            command = [
                "bash",
                str(script),
                "--role",
                role,
                "--source",
                str(snapshot),
                "--channel-source",
                str(self.source_root),
                "--hub-public-url",
                str(installation.get("hub_public_url") or "https://knoa.tinydotdot.com"),
                "--skip-pairing-qr",
            ]
            python_executable = str(installation.get("python_executable") or "").strip()
            if python_executable:
                command.extend(("--python", python_executable))
        self._run(command, environment=environment)
        self._wait_healthy(role)

    def status(self) -> dict[str, Any]:
        state = self._read_state()
        try:
            current = self._installed_commit(state)
        except SourceUpdateError:
            current = ""
        latest = str(state.get("last_checked_commit") or "")
        return {
            "channel": "source",
            "current_commit": current,
            "latest_commit": latest,
            "update_available": bool(current and latest and current != latest),
            "source_root": str(self.source_root),
        }

    def check(self) -> dict[str, Any]:
        state = self._read_state()
        current = self._installed_commit(state)
        _upstream, latest = self._fetch_latest()
        self._assert_fast_forward(current, latest)
        state.update(
            {
                "schema_version": 1,
                "current_commit": current,
                "last_checked_commit": latest,
            }
        )
        self._write_state(state)
        return self.status()

    def update(self) -> dict[str, Any]:
        state = self._read_state()
        previous = self._installed_commit(state)
        upstream, latest = self._fetch_latest()
        self._assert_fast_forward(previous, latest)
        if previous == latest:
            state.update(
                {
                    "schema_version": 1,
                    "current_commit": previous,
                    "last_checked_commit": latest,
                }
            )
            self._write_state(state)
            return self.status()
        self._git("merge", "--ff-only", upstream)
        if self._head() != latest:
            raise SourceUpdateError("source_fast_forward_failed")
        previous_snapshot = self._snapshot(previous)
        target_snapshot = self._snapshot(latest)
        installation = self._installation()
        try:
            self._install(target_snapshot, installation)
        except Exception:
            try:
                self._install(previous_snapshot, installation)
            except Exception as recovery_error:
                raise SourceUpdateError("source_update_and_recovery_failed") from recovery_error
            raise
        finally:
            self._discard_snapshot(target_snapshot)
            self._discard_snapshot(previous_snapshot)
        state.update(
            {
                "schema_version": 1,
                "current_commit": latest,
                "last_checked_commit": latest,
            }
        )
        self._write_state(state)
        return self.status()


__all__ = ["SourceUpdateError", "SourceUpdateManager"]
