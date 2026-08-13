"""Local owner administration for Secure Gateway pairing and devices."""
from __future__ import annotations

import sys
from datetime import UTC, datetime

from knoa_platform.config import load_config
from knoa_platform.gateway.audit import GatewayAuditRepository
from knoa_platform.gateway.identity import GatewayIdentityRepository
from knoa_platform.gateway.pairing import GatewayPairingPayload
from knoa_platform.runtime import RuntimePaths


def run_gateway_admin(
    config_path: str | None,
    *,
    action: str,
    principal_id: str | None,
    ttl_seconds: int = 300,
    device_id: str = "",
) -> int:
    """Execute one explicit local-only owner operation."""
    config = load_config(config_path) if config_path else load_config()
    principal = principal_id or config.owner_principal_id
    database = RuntimePaths.from_root(config.runtime_root).data / "gateway.db"
    identities = GatewayIdentityRepository(database)
    audit = GatewayAuditRepository(database)

    try:
        if action == "pair":
            grant = identities.create_pairing_grant(
                principal,
                ttl_seconds=ttl_seconds,
            )
            print(f"grant_id={grant.grant_id}")
            print(f"grant_secret={grant.secret}")
            print(f"expires_at={_timestamp(grant.expires_at)}")
            if config.gateway_public_url:
                payload = GatewayPairingPayload.from_grant(
                    grant,
                    config.gateway_public_url,
                ).encoded()
                print(f"pairing_json={payload}")
                _print_qr(payload)
            return 0

        if action == "devices":
            devices = identities.list_devices(principal)
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
            device = identities.revoke_device(principal, device_id)
            audit.append(
                "revoked",
                device_id=device.device_id,
                principal_id=device.principal_id,
                detail_code="local_admin",
            )
            print(f"revoked={device.device_id}")
            return 0
    except (LookupError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    raise ValueError(f"Unknown Gateway administration action: {action}")


def _timestamp(value: float) -> str:
    return datetime.fromtimestamp(value, UTC).isoformat()


def _print_qr(payload: str) -> None:
    import qrcode

    code = qrcode.QRCode(border=1)
    code.add_data(payload)
    code.make(fit=True)
    code.print_ascii(invert=True)
