"""Authentication strategies for the private Core transport."""
from __future__ import annotations

import hmac
from typing import Protocol

from knoa_platform.service.credentials import verify_principal_credential


class PrincipalAuthenticator(Protocol):
    async def authenticate(self, credential: str) -> str | None: ...


class StaticTokenAuthenticator:
    """Resolve configured credentials to principals using constant-time checks."""

    def __init__(self, credentials: dict[str, str]) -> None:
        if not credentials:
            raise ValueError("At least one TCP credential is required")
        normalized: list[tuple[str, str]] = []
        for credential, principal in credentials.items():
            if not credential.strip() or not principal.strip():
                raise ValueError("TCP credentials and principals must not be empty")
            normalized.append((credential, principal.strip()))
        self._credentials = tuple(normalized)

    async def authenticate(self, credential: str) -> str | None:
        for configured, principal in self._credentials:
            if hmac.compare_digest(credential, configured):
                return principal
        return None


class SignedPrincipalAuthenticator:
    """Authenticate short-lived principals issued by trusted local adapters."""

    def __init__(self, signing_key: str) -> None:
        if not signing_key.strip():
            raise ValueError("Signed principal authentication requires a key")
        self._signing_key = signing_key

    async def authenticate(self, credential: str) -> str | None:
        return verify_principal_credential(self._signing_key, credential)


class CompositeAuthenticator:
    """Try bounded authentication strategies in declared order."""

    def __init__(self, *authenticators: PrincipalAuthenticator) -> None:
        if not authenticators:
            raise ValueError("At least one authenticator is required")
        self._authenticators = authenticators

    async def authenticate(self, credential: str) -> str | None:
        for authenticator in self._authenticators:
            principal = await authenticator.authenticate(credential)
            if principal is not None:
                return principal
        return None
