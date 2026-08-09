"""Secure Gateway identity and transport boundaries."""

from pc_assistant.gateway.auth import (
    AuthenticatedGatewaySession,
    GatewayAuthenticationRejectedError,
    GatewayAuthenticationService,
    GatewayAuthRepository,
    GatewayChallenge,
    IssuedGatewaySession,
)
from pc_assistant.gateway.identity import (
    DeviceAlreadyPairedError,
    DeviceNotFoundError,
    GatewayDevice,
    GatewayIdentityRepository,
    PairingGrant,
    PairingGrantRejectedError,
)
from pc_assistant.gateway.adapter import SecureGatewayAdapter

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
]
