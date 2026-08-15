"""Discovery-driven onboarding for one standard MCP server connection."""

from __future__ import annotations

from dataclasses import dataclass

from knoa_platform.agent_runtime.config_control import ConfigurationController
from knoa_platform.extensions.manager import ExtensionManager
from knoa_platform.extensions.mcp import (
    MCPPromptDefinition,
    MCPResourceDefinition,
    MCPServerProvider,
    MCPToolDefinition,
    create_mcp_client,
)
from knoa_platform.extensions.mcp_resource_tasks import MCPResourceTaskBridge
from knoa_platform.extensions.models import (
    MCPResourceTaskConfig,
    MCPServerConfig,
    MCPToolPolicyConfig,
)
from knoa_platform.tools.base import ToolCapability, ToolEffect, ToolRisk


@dataclass(frozen=True)
class MCPOnboardingResult:
    server_id: str
    enabled_tools: tuple[str, ...]
    withheld_tools: tuple[str, ...]
    resources: tuple[MCPResourceDefinition, ...]
    prompts: tuple[MCPPromptDefinition, ...]
    state: str
    detail: str = ""


@dataclass(frozen=True)
class MCPInspectionResult:
    tools: tuple[MCPToolDefinition, ...]
    resources: tuple[MCPResourceDefinition, ...]
    prompts: tuple[MCPPromptDefinition, ...]


class MCPOnboardingService:
    """Discover, derive a fail-closed local policy, persist, and activate."""

    def __init__(
        self,
        extensions: ExtensionManager,
        config: ConfigurationController,
        resource_tasks: MCPResourceTaskBridge,
        providers: tuple[MCPServerProvider, ...] = (),
    ) -> None:
        self._extensions = extensions
        self._config = config
        self._resource_tasks = resource_tasks
        self._providers = {provider.server_id: provider for provider in providers}

    async def connect(
        self,
        server_id: str,
        connection: MCPServerConfig,
        enabled_tools: frozenset[str],
    ) -> MCPOnboardingResult:
        extension_id = f"mcp:{server_id}"
        if any(
            status.descriptor.extension_id == extension_id
            for status in self._extensions.statuses
        ):
            raise ValueError("MCP server is already configured")

        inspection = await self.inspect(connection)
        discovered = {tool.name: tool for tool in inspection.tools}
        unknown = enabled_tools - discovered.keys()
        if unknown:
            raise ValueError("Selected MCP Tool was not discovered")
        unsafe = [
            name
            for name in enabled_tools
            if discovered[name].read_only_hint is not True
        ]
        if unsafe:
            raise ValueError(
                "Only Tools explicitly annotated readOnlyHint=true may be enabled "
                "during automatic onboarding"
            )
        policies = {
            name: _read_only_policy(discovered[name]) for name in enabled_tools
        }
        configured = connection.model_copy(update={"tools": policies})
        provider = MCPServerProvider(server_id, configured)
        status = await self._extensions.add_provider(provider)
        if status.state.value != "running":
            raise ValueError(status.detail or "MCP server could not be activated")
        persisted = await self._config.add_mcp_server(server_id, configured)
        if not persisted.applied:
            await self._extensions.remove_provider(provider)
            raise ValueError(
                persisted.error or "MCP configuration could not be persisted"
            )
        self._providers[server_id] = provider
        enabled = tuple(sorted(policies))
        return MCPOnboardingResult(
            server_id=server_id,
            enabled_tools=enabled,
            withheld_tools=tuple(
                sorted(tool.name for tool in inspection.tools if tool.name not in policies)
            ),
            resources=inspection.resources,
            prompts=inspection.prompts,
            state=status.state.value,
            detail=status.detail,
        )

    async def disable_server(self, server_id: str) -> None:
        provider = self._providers.get(server_id)
        if provider is None:
            raise ValueError("MCP server is not active")
        persisted = await self._config.set_mcp_server_enabled(server_id, False)
        if not persisted.applied:
            raise ValueError(persisted.error or "MCP server could not be disabled")
        await self._extensions.stop_provider(provider)
        self._providers.pop(server_id, None)

    async def inspect(self, connection: MCPServerConfig) -> MCPInspectionResult:
        client = create_mcp_client(connection)
        try:
            await client.start()
            tools = await client.list_tools()
            resources = (
                await client.list_resources()
                if client.resource_capabilities().available
                else ()
            )
            try:
                prompts = await client.list_prompts()
            except Exception:  # noqa: BLE001 - prompts are an optional MCP capability
                prompts = ()
        finally:
            await client.close()
        return MCPInspectionResult(
            tools=tools,
            resources=resources,
            prompts=prompts,
        )

    async def configure_resource_task(
        self,
        server_id: str,
        route_id: str,
        route: MCPResourceTaskConfig,
    ) -> None:
        del server_id, route_id, route
        raise ValueError(
            "MCP Resource routing is configured by a Task Definition launch policy"
        )


def _read_only_policy(tool: MCPToolDefinition) -> MCPToolPolicyConfig:
    capabilities = {ToolCapability.MCP}
    if tool.open_world_hint is not False:
        capabilities.add(ToolCapability.NETWORK)
    return MCPToolPolicyConfig(
        effect=ToolEffect.READ_ONLY,
        capabilities=frozenset(capabilities),
        risk=ToolRisk.LOW,
    )
