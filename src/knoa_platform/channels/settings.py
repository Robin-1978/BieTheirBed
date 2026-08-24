"""Private, hot-reloadable configuration for user-facing channels."""

from __future__ import annotations

import json
import os
import secrets
import time
from pathlib import Path
from typing import Any

from knoa_platform.config import AppConfig
from knoa_platform.private_files import (
    fsync_directory,
    prepare_private_directory,
    restrict_private_file,
    validate_private_file,
)
from knoa_platform.secrets import SecretStore


class ChannelSettingsStore:
    def __init__(self, root: str | Path, *, clock=time.time) -> None:
        selected = Path(root).expanduser().resolve()
        self._path = selected / "data" / "channel-settings.json"
        self._secrets = SecretStore(selected / "secrets" / "channels", clock=clock)
        self._clock = clock

    def status(self, base: AppConfig, *, running: bool = False) -> dict[str, Any]:
        raw = self._load().get("dingtalk", {})
        secret_status = self._secrets.status("dingtalk.client_secret")
        configured_from_file = bool(secret_status["configured"])
        configured_from_base = bool(
            base.dingtalk_client_secret.get_secret_value().strip()
        )
        return {
            "enabled": bool(raw.get("enabled", base.dingtalk_enabled)),
            "client_id": str(raw.get("client_id", base.dingtalk_client_id)),
            "robot_code": str(raw.get("robot_code", base.dingtalk_robot_code)),
            "receive_id": str(raw.get("receive_id", base.dingtalk_receive_id)),
            "client_secret_configured": configured_from_file or configured_from_base,
            "client_secret_rotated_at": float(secret_status.get("rotated_at", 0)),
            "running": running,
            "updated_at": float(raw.get("updated_at", 0)),
        }

    def configure_dingtalk(
        self,
        base: AppConfig,
        *,
        enabled: bool,
        client_id: str,
        client_secret: str,
        robot_code: str,
        receive_id: str,
    ) -> AppConfig:
        normalized = {
            "enabled": bool(enabled),
            "client_id": self._text(client_id, "Client ID", required=enabled),
            "robot_code": self._text(robot_code, "Robot Code"),
            "receive_id": self._text(receive_id, "Receive ID"),
            "updated_at": float(self._clock()),
        }
        if client_secret:
            self._secrets.put("dingtalk.client_secret", client_secret)
        secret = self._secret(base)
        if enabled and not secret:
            raise ValueError("DingTalk Client Secret is required")
        document = self._load()
        document["dingtalk"] = normalized
        self._save(document)
        return self.apply(base)

    def apply(self, base: AppConfig) -> AppConfig:
        raw = self._load().get("dingtalk")
        if not isinstance(raw, dict):
            return base
        values = base.model_dump()
        values.update(
            dingtalk_enabled=bool(raw.get("enabled", False)),
            dingtalk_client_id=str(raw.get("client_id", "")),
            dingtalk_client_secret=self._secret(base),
            dingtalk_robot_code=str(raw.get("robot_code", "")),
            dingtalk_receive_id=str(raw.get("receive_id", "")),
        )
        return AppConfig.model_validate(values)

    def _secret(self, base: AppConfig) -> str:
        try:
            return self._secrets.get("dingtalk.client_secret").strip()
        except LookupError:
            return base.dingtalk_client_secret.get_secret_value().strip()

    def _load(self) -> dict[str, Any]:
        if not self._path.exists():
            return {}
        try:
            validate_private_file(self._path, label="Channel settings")
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, RuntimeError, ValueError, TypeError) as exc:
            raise RuntimeError("Channel settings are invalid") from exc
        return raw if isinstance(raw, dict) else {}

    def _save(self, document: dict[str, Any]) -> None:
        prepare_private_directory(self._path.parent, label="Channel settings directory")
        temporary = self._path.with_name(
            f".{self._path.name}.{secrets.token_hex(8)}.tmp"
        )
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump(document, stream, sort_keys=True, separators=(",", ":"))
                stream.flush()
                os.fsync(stream.fileno())
            restrict_private_file(temporary)
            os.replace(temporary, self._path)
            restrict_private_file(self._path)
            fsync_directory(self._path.parent)
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _text(value: str, label: str, *, required: bool = False) -> str:
        normalized = str(value).strip()
        if required and not normalized:
            raise ValueError(f"DingTalk {label} is required")
        if len(normalized) > 512 or any(ord(char) < 32 for char in normalized):
            raise ValueError(f"DingTalk {label} is invalid")
        return normalized


__all__ = ["ChannelSettingsStore"]
