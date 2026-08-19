from __future__ import annotations

import json
import subprocess
from pathlib import Path

import httpx
import pytest

from knoa_platform.host_lifecycle import HostLifecycleManager, create_lifecycle_app


class _FakeManager(HostLifecycleManager):
    def __init__(self, root: Path) -> None:
        for name in ("updater", "trust.json"):
            (root / name).write_text("test", encoding="utf-8")
        super().__init__(
            updater=root / "updater",
            release_root=root / "releases",
            trust_store=root / "trust.json",
            state_file=root / "host-state.json",
            incoming_root=root / "incoming",
        )
        self.active: set[str] = set()
        self.commands: list[list[str]] = []
        self.health_failures = 0

    def _run(self, command: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
        self.commands.append(command)
        if "current" in command:
            return subprocess.CompletedProcess(command, 0, "/release/current\n", "")
        return subprocess.CompletedProcess(command, 0, "", "")

    def _service_active(self, role):
        return role in self.active

    def _service(self, role, action):
        if action in {"start", "restart"}:
            self.active.add(role)
        elif action == "stop":
            self.active.discard(role)

    def _wait_healthy(self, roles, timeout_seconds=60.0):
        assert all(role in self.active for role in roles)
        if self.health_failures:
            self.health_failures -= 1
            raise RuntimeError("health_failed")


def test_manager_activates_and_deactivates_independent_roles(tmp_path: Path) -> None:
    manager = _FakeManager(tmp_path)

    activated = manager.act(manager_action("activate", role="node"))
    assert activated["installed_roles"] == ["node"]
    assert activated["services"]["node"]["active"] is True
    assert json.loads((tmp_path / "host-state.json").read_text())["installed_roles"] == ["node"]

    deactivated = manager.act(manager_action("deactivate", role="node"))
    assert deactivated["installed_roles"] == []
    assert deactivated["services"]["node"]["active"] is False


def manager_action(action: str, *, role: str | None = None, bundle_name: str | None = None):
    from knoa_platform.host_lifecycle import _Action

    return _Action(action=action, role=role, bundle_name=bundle_name)


@pytest.mark.asyncio
async def test_lifecycle_api_is_bearer_authenticated_and_returns_status(tmp_path: Path) -> None:
    manager = _FakeManager(tmp_path)
    app = create_lifecycle_app(manager, "t" * 48)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://localhost") as client:
        rejected = await client.get("/v1/lifecycle")
        accepted = await client.get(
            "/v1/lifecycle",
            headers={"Authorization": f"Bearer {'t' * 48}"},
        )
        action = await client.post(
            "/v1/lifecycle/actions",
            headers={"Authorization": f"Bearer {'t' * 48}"},
            json={"action": "activate", "role": "hub"},
        )

    assert rejected.status_code == 401
    assert accepted.status_code == 200
    assert action.status_code == 200
    assert action.json()["installed_roles"] == ["hub"]


def test_update_accepts_only_a_fixed_incoming_signed_bundle_path(tmp_path: Path) -> None:
    manager = _FakeManager(tmp_path)
    manager.act(manager_action("activate", role="hub"))
    bundle = manager.incoming_root / "knoa-host.zip"
    bundle.write_bytes(b"signed bundle placeholder")

    result = manager.act(manager_action("update", bundle_name=bundle.name))

    assert result["services"]["hub"]["active"] is True
    assert not bundle.exists()
    install = next(command for command in manager.commands if "install" in command)
    assert install[install.index("--role") + 1] == "all"
    with pytest.raises(ValueError, match="bundle_not_found"):
        manager.act(manager_action("update", bundle_name="missing.zip"))


def test_update_rejects_release_when_live_service_health_fails(tmp_path: Path) -> None:
    manager = _FakeManager(tmp_path)
    manager.act(manager_action("activate", role="node"))
    bundle = manager.incoming_root / "knoa-host.zip"
    bundle.write_bytes(b"signed bundle placeholder")
    manager.health_failures = 1

    with pytest.raises(RuntimeError, match="health_failed"):
        manager.act(manager_action("update", bundle_name=bundle.name))

    assert any("reject" in command for command in manager.commands)
    assert "node" in manager.active
