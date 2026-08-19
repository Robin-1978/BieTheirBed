from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
ROLE_CONTRACT = ROOT / "deploy" / "release" / "roles.json"

_EXPECTED: dict[str, dict[str, tuple[str, ...]]] = {
    "hub": {
        "services": ("hub",),
        "managed_workers": (),
        "embedded_ui": ("hub_console",),
        "data_roots": ("hub_root",),
    },
    "node": {
        "services": ("node_host",),
        "managed_workers": ("agent_runtime",),
        "embedded_ui": ("node_console",),
        "data_roots": ("node_root",),
    },
    "all": {
        "services": ("hub", "node_host"),
        "managed_workers": ("agent_runtime",),
        "embedded_ui": ("hub_console", "node_console"),
        "data_roots": ("hub_root", "node_root"),
    },
}


def load_contract(path: Path = ROLE_CONTRACT) -> dict[str, Any]:
    document = json.loads(path.read_text(encoding="utf-8"))
    if document.get("schema_version") != 1:
        raise ValueError("Unsupported release role schema")
    roles = document.get("roles")
    if not isinstance(roles, dict):
        raise TypeError("Release role contract must define roles")
    return document


def validate_role(role: str, document: dict[str, Any]) -> None:
    expected = _EXPECTED[role]
    actual = document["roles"].get(role)
    if not isinstance(actual, dict):
        raise TypeError(f"Release role '{role}' is missing")
    unknown = set(actual) - set(expected)
    if unknown:
        raise ValueError(f"Release role '{role}' has unknown fields: {sorted(unknown)}")
    for field, values in expected.items():
        actual_values = actual.get(field)
        if not isinstance(actual_values, list) or tuple(actual_values) != values:
            raise ValueError(
                f"Release role '{role}' field '{field}' must be {list(values)!r}"
            )
    forbidden_services = {"hub_console", "node_console", "agent_runtime"}
    if forbidden_services.intersection(actual["services"]):
        raise ValueError("Console and Agent Runtime cannot be standalone services")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--role", choices=tuple(_EXPECTED))
    args = parser.parse_args()
    document = load_contract()
    roles = (args.role,) if args.role else tuple(_EXPECTED)
    for role in roles:
        validate_role(role, document)
    print(f"release role contract ok: {', '.join(roles)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
