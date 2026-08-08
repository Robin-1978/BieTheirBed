"""Narrow persistent configuration control for the new runtime."""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import yaml

from pc_assistant.agent_runtime.contracts import ConfigSetRequest, ConfigSetResult
from pc_assistant.config import AppConfig


_MUTABLE_FIELDS = frozenset(
    {
        "context_window_budget",
        "llm_temperature",
        "max_iterations",
        "max_tokens",
        "max_total_tool_calls",
    }
)


class PersistentConfigController:
    """Validate and atomically persist explicit restart-bound overrides."""

    def __init__(self, config: AppConfig, override_path: str | Path) -> None:
        self._config = config
        self._path = Path(override_path).expanduser().resolve()
        self._lock = asyncio.Lock()

    async def set_config(self, request: ConfigSetRequest) -> ConfigSetResult:
        async with self._lock:
            return await asyncio.to_thread(self._set_config, request)

    def _set_config(self, request: ConfigSetRequest) -> ConfigSetResult:
        if request.field_name not in _MUTABLE_FIELDS:
            return ConfigSetResult(
                applied=False,
                error="Configuration field is not runtime-admin mutable",
            )
        candidate_data = self._config.model_dump(mode="python")
        candidate_data[request.field_name] = request.value
        try:
            candidate = AppConfig.model_validate(candidate_data)
        except ValueError:
            return ConfigSetResult(
                applied=False,
                error="Configuration value is invalid",
            )

        existing: dict[str, Any] = {}
        if self._path.exists():
            try:
                loaded = yaml.safe_load(self._path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    existing = loaded
            except (OSError, yaml.YAMLError):
                return ConfigSetResult(
                    applied=False,
                    error="Configuration override file is unreadable",
                )
        existing[request.field_name] = getattr(candidate, request.field_name)
        self._path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        temporary = self._path.with_name(f".{self._path.name}.tmp")
        try:
            temporary.write_text(
                yaml.safe_dump(existing, allow_unicode=True, sort_keys=True),
                encoding="utf-8",
            )
            temporary.chmod(0o600)
            temporary.replace(self._path)
        except OSError:
            temporary.unlink(missing_ok=True)
            return ConfigSetResult(
                applied=False,
                error="Configuration override could not be persisted",
            )
        self._config = candidate
        return ConfigSetResult(applied=True, restart_required=True)
