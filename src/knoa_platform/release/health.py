from __future__ import annotations

import argparse
import json
import platform

from knoa_agent_contracts import RuntimeTurnRequest
from knoa_platform import __version__
from knoa_platform.config import AppConfig
from knoa_platform.configuration.models import ManagedConfig
from knoa_platform.gateway.openapi import gateway_openapi_schema


def probe(role: str) -> dict[str, object]:
    if role not in {"hub", "node", "all"}:
        raise ValueError("Release health role must be hub, node or all")
    checks: list[str] = []
    if role in {"hub", "all"}:
        from knoa_platform.hub.app import HubApplication  # noqa: F401

        checks.append("hub_import")
    if role in {"node", "all"}:
        AppConfig.model_json_schema()
        ManagedConfig.model_json_schema()
        RuntimeTurnRequest.model_json_schema()
        gateway_openapi_schema()
        checks.extend(
            (
                "node_config_schema",
                "managed_config_schema",
                "agent_runtime_schema",
                "gateway_openapi",
            )
        )
    return {
        "healthy": True,
        "role": role,
        "version": __version__,
        "python": platform.python_version(),
        "checks": checks,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--role", choices=("hub", "node", "all"), required=True)
    args = parser.parse_args()
    print(json.dumps(probe(args.role), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
