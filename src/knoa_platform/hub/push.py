"""Hub-owned push delivery ports and the FCM HTTP v1 adapter."""
from __future__ import annotations

import base64
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode()


@dataclass(frozen=True)
class PushDeliveryResult:
    delivered: bool
    provider_message_id: str = ""
    error_code: str = ""
    permanent_token_failure: bool = False


class PushDeliveryPort(Protocol):
    async def deliver(self, token: str, message: dict[str, Any]) -> PushDeliveryResult: ...


class FCMHTTPv1PushDelivery:
    def __init__(self, service_account: dict[str, Any], *, clock=time.time) -> None:
        self._project_id = str(service_account["project_id"])
        self._client_email = str(service_account["client_email"])
        self._private_key = serialization.load_pem_private_key(
            str(service_account["private_key"]).encode(),
            password=None,
        )
        self._token_uri = str(
            service_account.get("token_uri") or "https://oauth2.googleapis.com/token"
        )
        self._clock = clock
        self._access_token = ""
        self._token_expires_at = 0.0

    @classmethod
    def from_environment(cls) -> FCMHTTPv1PushDelivery | None:
        path = os.environ.get("KNOA_FCM_SERVICE_ACCOUNT_FILE", "").strip()
        if not path:
            return None
        try:
            document = json.loads(Path(path).read_text(encoding="utf-8"))
            return cls(document)
        except (OSError, ValueError, KeyError, TypeError):
            return None

    async def _token(self) -> str:
        now = int(self._clock())
        if self._access_token and self._token_expires_at > now + 60:
            return self._access_token
        header = _b64(json.dumps(
            {"alg": "RS256", "typ": "JWT"}, separators=(",", ":")
        ).encode())
        claims = _b64(json.dumps({
            "iss": self._client_email,
            "scope": "https://www.googleapis.com/auth/firebase.messaging",
            "aud": self._token_uri,
            "iat": now,
            "exp": now + 3600,
        }, separators=(",", ":")).encode())
        unsigned = f"{header}.{claims}".encode()
        signature = self._private_key.sign(unsigned, padding.PKCS1v15(), hashes.SHA256())
        assertion = f"{header}.{claims}.{_b64(signature)}"
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(self._token_uri, data={
                "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                "assertion": assertion,
            })
            response.raise_for_status()
            payload = response.json()
        self._access_token = str(payload["access_token"])
        self._token_expires_at = now + int(payload.get("expires_in", 3600))
        return self._access_token

    async def deliver(self, token: str, message: dict[str, Any]) -> PushDeliveryResult:
        try:
            access_token = await self._token()
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.post(
                    f"https://fcm.googleapis.com/v1/projects/{self._project_id}/messages:send",
                    headers={"Authorization": f"Bearer {access_token}"},
                    json={"message": {"token": token, **message}},
                )
            payload = response.json() if response.content else {}
            if response.is_success:
                return PushDeliveryResult(
                    delivered=True,
                    provider_message_id=str(payload.get("name") or ""),
                )
            status = str((payload.get("error") or {}).get("status") or "fcm_rejected")
            permanent = response.status_code == 404 or status in {
                "UNREGISTERED", "INVALID_ARGUMENT",
            }
            return PushDeliveryResult(
                delivered=False,
                error_code=status.lower(),
                permanent_token_failure=permanent,
            )
        except (httpx.HTTPError, KeyError, ValueError, TypeError):
            return PushDeliveryResult(delivered=False, error_code="fcm_unavailable")


__all__ = [
    "FCMHTTPv1PushDelivery",
    "PushDeliveryPort",
    "PushDeliveryResult",
]
