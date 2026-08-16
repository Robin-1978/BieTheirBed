"""Secure Gateway identity and transport boundaries."""

from knoa_platform.gateway.auth import (
    AuthenticatedGatewaySession,
    GatewayAuthenticationRejectedError,
    GatewayAuthenticationService,
    GatewayAuthRepository,
    GatewayChallenge,
    IssuedGatewaySession,
)
from knoa_platform.gateway.identity import (
    DeviceAlreadyPairedError,
    DeviceNotFoundError,
    GatewayDevice,
    GatewayIdentityRepository,
    PairingGrant,
    PairingGrantRejectedError,
)

__all__ = [
    "AuthenticatedGatewaySession",
    "DeviceAlreadyPairedError",
    "DeviceNotFoundError",
    "GatewayAuthRepository",
    "GatewayAuthenticationRejectedError",
    "GatewayAuthenticationService",
    "GatewayChallenge",
    "GatewayCoreBridge",
    "GatewayDevice",
    "GatewayIdentityRepository",
    "IssuedGatewaySession",
    "PairingGrant",
    "PairingGrantRejectedError",
    "SecureGatewayAdapter",
]


def __getattr__(name: str):
    if name == "SecureGatewayAdapter":
        from knoa_platform.gateway.adapter import SecureGatewayAdapter

        return SecureGatewayAdapter
    if name == "GatewayCoreBridge":
        from knoa_platform.gateway.core import GatewayCoreBridge

        return GatewayCoreBridge
    raise AttributeError(name)
