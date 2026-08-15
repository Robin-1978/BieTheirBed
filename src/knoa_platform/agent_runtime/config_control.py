"""Adapters that route operational convenience writes through ConfigurationService."""

from __future__ import annotations

import asyncio

from knoa_platform.agent_runtime.contracts import ConfigSetRequest, ConfigSetResult
from knoa_platform.configuration import ConfigurationService
from knoa_platform.configuration.models import ManagedMCPConfig
from knoa_platform.extensions.models import MCPResourceTaskConfig, MCPServerConfig

_OPERATIONAL_FIELDS = {
    "context_window_budget": "context_window_budget",
    "llm_temperature": "llm_temperature",
    "max_iterations": "max_iterations",
    "max_tokens": "max_output_tokens",
    "max_total_tool_calls": "max_total_tool_calls",
}


class ConfigurationController:
    """Narrow convenience adapter; ConfigurationService remains the only writer."""

    def __init__(self, configuration: ConfigurationService) -> None:
        self._configuration = configuration
        self._lock = asyncio.Lock()

    async def set_config(self, request: ConfigSetRequest) -> ConfigSetResult:
        target = _OPERATIONAL_FIELDS.get(request.field_name)
        if target is None:
            return ConfigSetResult(
                applied=False,
                error="Configuration field is not operationally mutable",
            )
        async with self._lock:
            current = self._configuration.current().document
            try:
                payload = current.operational.model_dump(mode="python")
                payload[target] = request.value
                operational = type(current.operational).model_validate(payload)
                candidate = current.model_copy(update={"operational": operational})
                return await self._publish(
                    candidate,
                    f"Update operational setting {target}",
                )
            except (TypeError, ValueError):
                return ConfigSetResult(
                    applied=False,
                    error="Configuration value is invalid",
                )

    async def add_mcp_server(
        self,
        server_id: str,
        server: MCPServerConfig,
    ) -> ConfigSetResult:
        async with self._lock:
            current = self._configuration.current().document
            if server_id in current.mcp_servers:
                return ConfigSetResult(
                    applied=False,
                    error="MCP server is already configured",
                )
            managed = ManagedMCPConfig(
                transport=server.transport,
                enabled=server.enabled,
                command=(server.command, *server.args) if server.command else (),
                url=server.url,
                working_directory=server.working_directory,
                inherit_env=server.inherit_env,
                optional_env=server.optional_env,
                secret_refs={
                    name: f"environment.{name}"
                    for name in (*server.inherit_env, *server.optional_env)
                },
                timeout_seconds=server.timeout_seconds,
                tools={
                    name: {
                        "effect": policy.effect.value,
                        "capabilities": frozenset(
                            capability.value for capability in policy.capabilities
                        ),
                        "risk": policy.risk.value,
                    }
                    for name, policy in server.tools.items()
                },
            )
            candidate = current.model_copy(
                update={
                    "mcp_servers": {
                        **current.mcp_servers,
                        server_id: managed,
                    }
                }
            )
            return await self._publish(candidate, f"Add MCP server {server_id}")

    async def add_mcp_resource_task(
        self,
        server_id: str,
        route_id: str,
        route: MCPResourceTaskConfig,
    ) -> ConfigSetResult:
        del server_id, route_id, route
        return ConfigSetResult(
            applied=False,
            error=(
                "MCP Resource routing is configured by a Task Definition "
                "launch policy"
            ),
        )

    async def set_mcp_server_enabled(
        self,
        server_id: str,
        enabled: bool,
    ) -> ConfigSetResult:
        async with self._lock:
            current = self._configuration.current().document
            server = current.mcp_servers.get(server_id)
            if server is None:
                return ConfigSetResult(
                    applied=False,
                    error="MCP server is not configured",
                )
            candidate = current.model_copy(
                update={
                    "mcp_servers": {
                        **current.mcp_servers,
                        server_id: server.model_copy(update={"enabled": enabled}),
                    }
                }
            )
            return await self._publish(
                candidate,
                f"{'Enable' if enabled else 'Disable'} MCP server {server_id}",
            )

    async def _publish(self, document, summary: str) -> ConfigSetResult:
        draft = self._configuration.create_draft(actor="control_adapter")
        draft = self._configuration.replace_draft(
            draft.draft_id,
            document,
            expected_version=draft.draft_version,
            actor="control_adapter",
        )
        result = await self._configuration.publish(
            draft.draft_id,
            expected_version=draft.draft_version,
            actor="control_adapter",
            summary=summary,
        )
        applied = result.state.applied_revision_id == result.revision.revision_id
        return ConfigSetResult(
            applied=applied,
            restart_required=False,
            error="" if applied else result.state.apply_error_code,
        )
