from __future__ import annotations

from pathlib import Path

import pytest

from knoa_platform.agent_runtime.config_control import ConfigurationController
from knoa_platform.agent_runtime.contracts import ConfigSetRequest
from knoa_platform.config import AppConfig
from knoa_platform.configuration import ConfigRegistry, ConfigurationService


def _controller(tmp_path: Path, *, applier=None):
    kwargs = {} if applier is None else {"applier": applier}
    service = ConfigurationService(
        ConfigRegistry(tmp_path / "config.db"),
        AppConfig().managed_config(),
        bootstrap_actor="test",
        **kwargs,
    )
    return ConfigurationController(service), service


@pytest.mark.asyncio
async def test_config_controller_publishes_operational_revision_live(tmp_path: Path) -> None:
    applied = []

    async def apply(_previous, revision):
        applied.append(revision.revision_id)

    controller, configuration = _controller(tmp_path, applier=apply)
    result = await controller.set_config(
        ConfigSetRequest(field_name="max_iterations", value=14)
    )

    assert result.applied
    assert not result.restart_required
    assert configuration.current().document.operational.max_iterations == 14
    assert applied == [configuration.current().revision_id]


@pytest.mark.asyncio
async def test_config_controller_rejects_unknown_or_wrong_typed_values(tmp_path: Path) -> None:
    controller, configuration = _controller(tmp_path)
    initial_revision = configuration.current().revision_id

    unknown = await controller.set_config(
        ConfigSetRequest(field_name="service_token", value="new-secret")
    )
    invalid = await controller.set_config(
        ConfigSetRequest(field_name="max_iterations", value="many")
    )

    assert not unknown.applied
    assert not invalid.applied
    assert configuration.current().revision_id == initial_revision


@pytest.mark.asyncio
async def test_config_registry_remains_authoritative_across_service_restart(tmp_path: Path) -> None:
    database = tmp_path / "config.db"
    first = ConfigurationService(
        ConfigRegistry(database),
        AppConfig(max_iterations=4).managed_config(),
        bootstrap_actor="test",
    )
    result = await ConfigurationController(first).set_config(
        ConfigSetRequest(field_name="max_iterations", value=16)
    )
    assert result.applied

    restarted = ConfigurationService(
        ConfigRegistry(database),
        AppConfig(max_iterations=2).managed_config(),
        bootstrap_actor="test",
    )

    assert restarted.current().document.operational.max_iterations == 16
