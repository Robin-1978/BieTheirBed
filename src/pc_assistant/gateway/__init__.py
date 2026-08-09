"""Secure Gateway identity and transport boundaries."""

from pc_assistant.gateway.identity import (
    DeviceAlreadyPairedError,
    DeviceNotFoundError,
    GatewayDevice,
    GatewayIdentityRepository,
    PairingGrant,
    PairingGrantRejectedError,
)

__all__ = [
    "DeviceAlreadyPairedError",
    "DeviceNotFoundError",
    "GatewayDevice",
    "GatewayIdentityRepository",
    "PairingGrant",
    "PairingGrantRejectedError",
]
