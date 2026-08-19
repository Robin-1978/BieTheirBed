from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import tempfile
from pathlib import Path
from typing import Any

import yaml
from pydantic import TypeAdapter

from knoa_agent_contracts import (
    CreateRuntimeSession,
    ReconcileRuntime,
    ResolveRuntimeInteraction,
    ResumeRuntimeSession,
    RuntimeInterruptCommand,
    RuntimeSteerCommand,
    RuntimeTurnEvent,
    RuntimeTurnRequest,
)
from knoa_platform.agent_runtime.composition import build_core_runtime
from knoa_platform.config import AppConfig
from knoa_platform.configuration.models import ManagedConfig
from knoa_platform.gateway.audit import GatewayAuditRepository
from knoa_platform.gateway.auth import GatewayAuthRepository
from knoa_platform.gateway.identity import GatewayIdentityRepository
from knoa_platform.gateway.openapi import gateway_openapi_schema
from knoa_platform.hub.hosted import HostedControlRepository
from knoa_platform.hub.repository import HubRepository
from knoa_platform.relay_protocol import ClientHello, PairingClientHello
from knoa_platform.resource_protocol import ResourceClientHello

ROOT = Path(__file__).resolve().parents[1]
BASELINE = ROOT / "protocol" / "baseline" / "runtime-v1.json"


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _sqlite_schema(path: Path) -> list[dict[str, str]]:
    with sqlite3.connect(path) as connection:
        rows = connection.execute(
            """SELECT type, name, tbl_name, sql
               FROM sqlite_master
               WHERE name NOT LIKE 'sqlite_%' AND sql IS NOT NULL
               ORDER BY type, name"""
        ).fetchall()
    return [
        {
            "type": str(row[0]),
            "name": str(row[1]),
            "table": str(row[2]),
            "sql": " ".join(str(row[3]).split()),
        }
        for row in rows
    ]


def _runtime_contract_schema() -> dict[str, Any]:
    models = {
        "create_session": CreateRuntimeSession.model_json_schema(),
        "resume_session": ResumeRuntimeSession.model_json_schema(),
        "start_turn": RuntimeTurnRequest.model_json_schema(),
        "steer_turn": RuntimeSteerCommand.model_json_schema(),
        "interrupt_turn": RuntimeInterruptCommand.model_json_schema(),
        "resolve_interaction": ResolveRuntimeInteraction.model_json_schema(),
        "reconcile": ReconcileRuntime.model_json_schema(),
        "runtime_event": TypeAdapter(RuntimeTurnEvent).json_schema(),
    }
    return models


def _relay_transcripts() -> dict[str, Any]:
    ticket = "t" * 100
    public_key = "p" * 43
    ephemeral_key = "e" * 43
    nonce = "n" * 32
    signature = "s" * 86
    return {
        "app_session": ClientHello(
            ticket=ticket,
            installation_id="installation-fixture-1",
            device_id="device-fixture-1",
            client_signing_public_key=public_key,
            client_ephemeral_public_key=ephemeral_key,
            client_nonce=nonce,
            signature=signature,
        ).transcript(),
        "app_pairing": PairingClientHello(
            ticket=ticket,
            installation_id="installation-fixture-1",
            client_signing_public_key=public_key,
            client_ephemeral_public_key=ephemeral_key,
            client_nonce=nonce,
            signature=signature,
        ).transcript(),
        "node_resource": ResourceClientHello(
            ticket=ticket,
            caller_node_id="node-caller-fixture-1",
            client_ephemeral_public_key=ephemeral_key,
            client_nonce=nonce,
            signature=signature,
        ).transcript(),
    }


def build_baseline() -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="knoa-baseline-") as directory:
        root = Path(directory)
        defaults = yaml.safe_load((ROOT / "config" / "default.yaml").read_text())
        defaults["runtime_root"] = str(root / "node")
        config = AppConfig(**defaults)
        build_core_runtime(config)
        node_database = root / "node" / "data" / "assistant.db"

        gateway_database = root / "node" / "data" / "gateway.db"
        GatewayIdentityRepository(gateway_database)
        GatewayAuthRepository(gateway_database)
        GatewayAuditRepository(gateway_database)

        self_hub_database = root / "self-hub" / "hub.db"
        HubRepository(self_hub_database, hub_id="hub-fixture-1")
        hosted_database = root / "hosted-hub" / "control.db"
        HostedControlRepository(hosted_database, hub_id="hub-fixture-1")

        sqlite_schemas = {
            "node_authority": _sqlite_schema(node_database),
            "node_gateway": _sqlite_schema(gateway_database),
            "self_hosted_hub": _sqlite_schema(self_hub_database),
            "hosted_control": _sqlite_schema(hosted_database),
        }

    managed_schema = ManagedConfig.model_json_schema()
    runtime_schema = _runtime_contract_schema()
    gateway_schema = gateway_openapi_schema()
    relay_transcripts = _relay_transcripts()
    return {
        "schema_version": 1,
        "contracts": {
            "gateway_openapi_sha256": _sha256(gateway_schema),
            "managed_config_schema_sha256": _sha256(managed_schema),
            "agent_runtime_schema_sha256": _sha256(runtime_schema),
            "relay_transcripts_sha256": _sha256(relay_transcripts),
        },
        "relay_transcripts": relay_transcripts,
        "sqlite_schemas": sqlite_schemas,
        "sqlite_schema_sha256": {
            name: _sha256(schema) for name, schema in sqlite_schemas.items()
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--update", action="store_true")
    args = parser.parse_args()
    actual = build_baseline()
    encoded = json.dumps(actual, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    if args.update:
        BASELINE.parent.mkdir(parents=True, exist_ok=True)
        BASELINE.write_text(encoded, encoding="utf-8")
    elif not BASELINE.is_file() or BASELINE.read_text(encoding="utf-8") != encoded:
        raise RuntimeError(
            "Runtime baseline changed; review schema compatibility and run "
            "scripts/capture_runtime_baseline.py --update"
        )
    print("runtime baseline ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
