"""Canonical local-to-mobile Gateway pairing payload."""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from pc_assistant.gateway.identity import PairingGrant


class GatewayPairingPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    version: str = "v1"
    gateway_url: str = Field(min_length=1, max_length=2048)
    grant_id: str = Field(min_length=1, max_length=128)
    grant_secret: str = Field(min_length=32, max_length=256)
    expires_at: float

    @classmethod
    def from_grant(
        cls,
        grant: PairingGrant,
        gateway_url: str,
    ) -> GatewayPairingPayload:
        return cls(
            gateway_url=gateway_url.rstrip("/"),
            grant_id=grant.grant_id,
            grant_secret=grant.secret,
            expires_at=grant.expires_at,
        )

    def encoded(self) -> str:
        return self.model_dump_json()
