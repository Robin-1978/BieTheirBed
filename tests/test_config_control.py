from __future__ import annotations

import stat
from pathlib import Path

import pytest
import yaml

from knoa_platform.agent_runtime.config_control import PersistentConfigController
from knoa_platform.agent_runtime.contracts import ConfigSetRequest
from knoa_platform.config import AppConfig, load_config


@pytest.mark.asyncio
async def test_config_controller_persists_only_explicit_override(tmp_path: Path) -> None:
    path = tmp_path / "config" / "local.yaml"
    controller = PersistentConfigController(AppConfig(), path)

    result = await controller.set_config(
        ConfigSetRequest(field_name="max_iterations", value=14)
    )

    assert result.applied and result.restart_required
    assert yaml.safe_load(path.read_text()) == {"max_iterations": 14}
    assert "api_key" not in path.read_text()
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


@pytest.mark.asyncio
async def test_config_controller_rejects_unknown_or_wrong_typed_values(
    tmp_path: Path,
) -> None:
    controller = PersistentConfigController(AppConfig(), tmp_path / "local.yaml")

    unknown = await controller.set_config(
        ConfigSetRequest(field_name="service_token", value="new-secret")
    )
    invalid = await controller.set_config(
        ConfigSetRequest(field_name="max_iterations", value="many")
    )

    assert not unknown.applied
    assert not invalid.applied
    assert not (tmp_path / "local.yaml").exists()


@pytest.mark.asyncio
async def test_explicit_config_does_not_change_existing_parent_permissions(
    tmp_path: Path,
) -> None:
    selected_parent = tmp_path / "project"
    selected_parent.mkdir(mode=0o755)
    selected_parent.chmod(0o755)
    path = selected_parent / "assistant.yaml"
    controller = PersistentConfigController(AppConfig(), path)

    result = await controller.set_config(
        ConfigSetRequest(field_name="max_iterations", value=18)
    )

    assert result.applied
    assert stat.S_IMODE(selected_parent.stat().st_mode) == 0o755
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


@pytest.mark.asyncio
async def test_persisted_override_is_loaded_after_restart(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runtime_root = tmp_path / "runtime"
    monkeypatch.setenv("KNOA_HOME", str(runtime_root))
    monkeypatch.delenv("KNOA_RUNTIME_ROOT", raising=False)
    initial = load_config()
    controller = PersistentConfigController(
        initial,
        runtime_root / "config" / "local.yaml",
    )

    result = await controller.set_config(
        ConfigSetRequest(field_name="max_iterations", value=14)
    )
    restarted = load_config()

    assert result.applied and result.restart_required
    assert restarted.max_iterations == 14


@pytest.mark.asyncio
async def test_explicit_config_remains_authoritative_after_admin_update(
    tmp_path: Path,
) -> None:
    explicit = tmp_path / "assistant.yaml"
    explicit.write_text("max_iterations: 4\n", encoding="utf-8")
    initial = load_config(explicit)
    controller = PersistentConfigController(initial, initial.source_config_path)

    result = await controller.set_config(
        ConfigSetRequest(field_name="max_iterations", value=16)
    )
    restarted = load_config(explicit)

    assert result.applied
    assert restarted.max_iterations == 16
