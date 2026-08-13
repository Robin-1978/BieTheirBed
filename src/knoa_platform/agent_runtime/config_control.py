"""Narrow persistent configuration control for the new runtime."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import yaml

from knoa_platform.agent_runtime.contracts import ConfigSetRequest, ConfigSetResult
from knoa_platform.config import AppConfig
from knoa_platform.extensions.models import MCPResourceTaskConfig, MCPServerConfig

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

    async def add_mcp_server(
        self,
        server_id: str,
        server: MCPServerConfig,
    ) -> ConfigSetResult:
        async with self._lock:
            return await asyncio.to_thread(self._add_mcp_server, server_id, server)

    def _add_mcp_server(
        self,
        server_id: str,
        server: MCPServerConfig,
    ) -> ConfigSetResult:
        if server_id in self._config.mcp_servers:
            return ConfigSetResult(
                applied=False, error="MCP server is already configured"
            )
        candidate_data = self._config.model_dump(mode="python")
        candidate_data["mcp_servers"] = {
            **self._config.mcp_servers,
            server_id: server,
        }
        try:
            candidate = AppConfig.model_validate(candidate_data)
        except ValueError:
            return ConfigSetResult(
                applied=False, error="MCP server configuration is invalid"
            )
        existing = self._read_existing()
        if isinstance(existing, ConfigSetResult):
            return existing
        configured = dict(existing.get("mcp_servers") or {})
        configured[server_id] = server.model_dump(mode="json", exclude_defaults=True)
        existing["mcp_servers"] = configured
        result = self._write_existing(existing)
        if result is not None:
            return result
        self._config = candidate
        return ConfigSetResult(applied=True, restart_required=False)

    async def add_mcp_resource_task(
        self,
        server_id: str,
        route_id: str,
        route: MCPResourceTaskConfig,
    ) -> ConfigSetResult:
        async with self._lock:
            return await asyncio.to_thread(
                self._add_mcp_resource_task,
                server_id,
                route_id,
                route,
            )

    def _add_mcp_resource_task(
        self,
        server_id: str,
        route_id: str,
        route: MCPResourceTaskConfig,
    ) -> ConfigSetResult:
        server = self._config.mcp_servers.get(server_id)
        if server is None:
            return ConfigSetResult(applied=False, error="MCP server is not configured")
        if route_id in server.resource_tasks:
            return ConfigSetResult(
                applied=False,
                error="MCP Resource Task route is already configured",
            )
        updated_server = server.model_copy(
            update={"resource_tasks": {**server.resource_tasks, route_id: route}}
        )
        candidate_data = self._config.model_dump(mode="python")
        candidate_data["mcp_servers"] = {
            **self._config.mcp_servers,
            server_id: updated_server,
        }
        try:
            candidate = AppConfig.model_validate(candidate_data)
        except ValueError:
            return ConfigSetResult(applied=False, error="MCP Resource Task is invalid")
        existing = self._read_existing()
        if isinstance(existing, ConfigSetResult):
            return existing
        configured = dict(existing.get("mcp_servers") or {})
        configured[server_id] = updated_server.model_dump(
            mode="json",
            exclude_defaults=True,
        )
        existing["mcp_servers"] = configured
        result = self._write_existing(existing)
        if result is not None:
            return result
        self._config = candidate
        return ConfigSetResult(applied=True, restart_required=False)

    async def set_mcp_server_enabled(
        self,
        server_id: str,
        enabled: bool,
    ) -> ConfigSetResult:
        async with self._lock:
            return await asyncio.to_thread(
                self._set_mcp_server_enabled,
                server_id,
                enabled,
            )

    def _set_mcp_server_enabled(
        self,
        server_id: str,
        enabled: bool,
    ) -> ConfigSetResult:
        server = self._config.mcp_servers.get(server_id)
        if server is None:
            return ConfigSetResult(applied=False, error="MCP server is not configured")
        updated_server = server.model_copy(update={"enabled": enabled})
        candidate_data = self._config.model_dump(mode="python")
        candidate_data["mcp_servers"] = {
            **self._config.mcp_servers,
            server_id: updated_server,
        }
        candidate = AppConfig.model_validate(candidate_data)
        existing = self._read_existing()
        if isinstance(existing, ConfigSetResult):
            return existing
        configured = dict(existing.get("mcp_servers") or {})
        configured[server_id] = updated_server.model_dump(
            mode="json",
            exclude_defaults=True,
        )
        existing["mcp_servers"] = configured
        result = self._write_existing(existing)
        if result is not None:
            return result
        self._config = candidate
        return ConfigSetResult(applied=True, restart_required=False)

    def _read_existing(self) -> dict[str, Any] | ConfigSetResult:
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
        return existing

    def _write_existing(self, existing: dict[str, Any]) -> ConfigSetResult | None:
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
        return None

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

        existing = self._read_existing()
        if isinstance(existing, ConfigSetResult):
            return existing
        existing[request.field_name] = getattr(candidate, request.field_name)
        write_error = self._write_existing(existing)
        if write_error is not None:
            return write_error
        self._config = candidate
        return ConfigSetResult(applied=True, restart_required=True)
