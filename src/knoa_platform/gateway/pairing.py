"""Canonical local-to-mobile Gateway pairing payload."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from knoa_platform.gateway.identity import PairingGrant


class GatewayPairingPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    version: Literal["v3"] = "v3"
    transport: Literal["direct", "relay"]
    gateway_url: str = Field(min_length=1, max_length=2048)
    node_id: str = Field(min_length=1, max_length=128)
    node_signing_public_key: str = Field(min_length=40, max_length=64)
    node_configuration_public_key: str = Field(min_length=40, max_length=64)
    grant_id: str = Field(min_length=1, max_length=128)
    grant_secret: str = Field(min_length=32, max_length=256)
    expires_at: float

    @classmethod
    def from_grant(
        cls,
        grant: PairingGrant,
        gateway_url: str,
        *,
        transport: Literal["direct", "relay"],
        node_id: str,
        node_signing_public_key: str,
        node_configuration_public_key: str,
    ) -> GatewayPairingPayload:
        return cls(
            gateway_url=gateway_url.rstrip("/"),
            transport=transport,
            node_id=node_id,
            node_signing_public_key=node_signing_public_key,
            node_configuration_public_key=node_configuration_public_key,
            grant_id=grant.grant_id,
            grant_secret=grant.secret,
            expires_at=grant.expires_at,
        )

    def encoded(self) -> str:
        return self.model_dump_json()
