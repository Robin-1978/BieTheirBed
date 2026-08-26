from __future__ import annotations

from pathlib import Path

import pytest

from knoa_platform.agent_runtime.contracts import RuntimeScope
from knoa_platform.config import AppConfig
from knoa_platform.configuration import ConfigRegistry, ConfigurationService
from knoa_platform.tools.configuration import ConfigurationTool


def _service(tmp_path: Path, *, enabled: bool = False) -> ConfigurationService:
    document = AppConfig().managed_config()
    document = document.model_copy(
        update={
            "operational": document.operational.model_copy(
                update={"agent_configuration_enabled": enabled}
            )
        }
    )
    return ConfigurationService(
        ConfigRegistry(tmp_path / "config.db"), document, bootstrap_actor="test"
    )


@pytest.mark.asyncio
async def test_configuration_tool_is_disabled_by_default(tmp_path: Path) -> None:
    service = _service(tmp_path)
    tool = ConfigurationTool(service)
    assert not tool.policy.configured


@pytest.mark.asyncio
async def test_configuration_tool_creates_draft_and_publishes_after_enable(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path, enabled=True)
    tool = ConfigurationTool(service)
    scope = RuntimeScope(principal_id="local", session_handle="s")

    described = await tool.execute_scoped(scope, action="describe")
    assert "approval_review" in described["sections"]
    proposed = await tool.execute_scoped(
        scope,
        action="propose",
        changes={"operational": {"max_iterations": 40}},
    )
    assert proposed["draft_id"]
    assert any(change["path"] == "/operational/max_iterations" for change in proposed["changes"])
    published = await tool.execute_scoped(
        scope,
        action="publish",
        draft_id=proposed["draft_id"],
        expected_version=proposed["draft_version"],
    )
    assert published["state"]["apply_status"] == "idle"
    assert service.current().document.operational.max_iterations == 40
