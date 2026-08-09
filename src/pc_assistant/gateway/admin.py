"""Local owner administration for Secure Gateway pairing and devices."""
from __future__ import annotations

from datetime import UTC, datetime
import sys

from pc_assistant.config import load_config
from pc_assistant.gateway.identity import GatewayIdentityRepository
from pc_assistant.runtime import RuntimePaths


def run_gateway_admin(
    config_path: str | None,
    *,
    action: str,
    principal_id: str,
    ttl_seconds: int = 300,
    device_id: str = "",
) -> int:
    """Execute one explicit local-only owner operation."""
    config = load_config(config_path) if config_path else load_config()
    database = RuntimePaths.from_root(config.runtime_root).data / "gateway.db"
    identities = GatewayIdentityRepository(database)

    try:
        if action == "pair":
            grant = identities.create_pairing_grant(
                principal_id,
                ttl_seconds=ttl_seconds,
            )
            print(f"grant_id={grant.grant_id}")
            print(f"grant_secret={grant.secret}")
            print(f"expires_at={_timestamp(grant.expires_at)}")
            return 0

        if action == "devices":
            devices = identities.list_devices(principal_id)
            if not devices:
                print("No paired devices.")
                return 0
            for device in devices:
                last_seen = "never" if device.last_seen_at is None else _timestamp(
                    device.last_seen_at
                )
                print(
                    f"{device.device_id}\t{device.state}\t{device.display_name}"
                    f"\tlast_seen={last_seen}"
                )
            return 0

        if action == "revoke":
            device = identities.revoke_device(principal_id, device_id)
            print(f"revoked={device.device_id}")
            return 0
    except (LookupError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    raise ValueError(f"Unknown Gateway administration action: {action}")


def _timestamp(value: float) -> str:
    return datetime.fromtimestamp(value, UTC).isoformat()
