from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "check_release_role.py"
SPEC = importlib.util.spec_from_file_location("check_release_role", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


@pytest.mark.parametrize("role", ("hub", "node", "all"))
def test_release_role_contract(role: str) -> None:
    MODULE.validate_role(role, MODULE.load_contract())


def test_consoles_and_agent_runtime_are_not_standalone_services() -> None:
    roles = MODULE.load_contract()["roles"]
    services = {service for role in roles.values() for service in role["services"]}
    assert "hub_console" not in services
    assert "node_console" not in services
    assert "agent_runtime" not in services
