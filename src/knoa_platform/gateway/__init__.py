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
from knoa_platform.gateway.adapter import SecureGatewayAdapter
from knoa_platform.gateway.core import GatewayCoreBridge

__all__ = [
    "AuthenticatedGatewaySession",
    "DeviceAlreadyPairedError",
    "DeviceNotFoundError",
    "GatewayDevice",
    "GatewayAuthenticationRejectedError",
    "GatewayAuthenticationService",
    "GatewayAuthRepository",
    "GatewayChallenge",
    "GatewayIdentityRepository",
    "IssuedGatewaySession",
    "PairingGrant",
    "PairingGrantRejectedError",
    "SecureGatewayAdapter",
    "GatewayCoreBridge",
]
