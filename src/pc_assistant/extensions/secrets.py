"""Private local Secret resolution for authenticated extensions."""
from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Protocol

from pc_assistant.extensions.models import SECRET_ID_PATTERN


_MAX_SECRET_BYTES = 16 * 1024


class SecretUnavailableError(RuntimeError):
    """Raised without secret material when a configured Secret cannot be used."""


@dataclass(frozen=True, repr=False)
class SecretValue:
    _value: str

    def reveal(self) -> str:
        return self._value

    def __repr__(self) -> str:
        return "SecretValue('**********')"

    def __str__(self) -> str:
        return "**********"


class SecretResolver(Protocol):
    def resolve(self, secret_id: str) -> SecretValue: ...


class PrivateFileSecretStore:
    """Resolve stable Secret IDs from process env or owner-only local files."""

    def __init__(
        self,
        root: str | Path,
        *,
        environment: Mapping[str, str] | None = None,
    ) -> None:
        self._root = Path(root).expanduser().resolve()
        self._environment = os.environ if environment is None else environment

    @staticmethod
    def _normalize_id(secret_id: str) -> str:
        normalized = secret_id.strip()
        if not SECRET_ID_PATTERN.fullmatch(normalized):
            raise SecretUnavailableError("Secret ID is invalid")
        return normalized

    @staticmethod
    def environment_name(secret_id: str) -> str:
        return "PC_SECRET_" + secret_id.upper().replace("-", "_")

    def resolve(self, secret_id: str) -> SecretValue:
        normalized = self._normalize_id(secret_id)
        env_name = self.environment_name(normalized)
        env_value = self._environment.get(env_name, "").strip()
        if env_value:
            return SecretValue(env_value)

        path = self._root / f"{normalized}.secret"
        if path.is_symlink():
            raise SecretUnavailableError("Secret file must not be a symbolic link")
        try:
            metadata = path.stat()
        except FileNotFoundError as exc:
            raise SecretUnavailableError("Configured Secret is unavailable") from exc
        if not stat.S_ISREG(metadata.st_mode):
            raise SecretUnavailableError("Secret path must be a regular file")
        if stat.S_IMODE(metadata.st_mode) != 0o600:
            raise SecretUnavailableError("Secret file must use mode 0600")
        if hasattr(os, "getuid") and metadata.st_uid != os.getuid():
            raise SecretUnavailableError("Secret file must be owned by the current user")
        with path.open("rb") as stream:
            raw = stream.read(_MAX_SECRET_BYTES + 1)
        if len(raw) > _MAX_SECRET_BYTES:
            raise SecretUnavailableError("Secret file exceeds the size limit")
        try:
            value = raw.decode("utf-8").strip()
        except UnicodeDecodeError as exc:
            raise SecretUnavailableError("Secret file must contain UTF-8 text") from exc
        if not value:
            raise SecretUnavailableError("Configured Secret is empty")
        return SecretValue(value)
