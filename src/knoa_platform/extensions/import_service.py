"""Owner-operated Extension import boundary ending in a Config Draft."""

from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from urllib.parse import urlparse

from knoa_platform.configuration.models import (
    ConfigDraft,
    ManagedConfig,
    ManagedMCPConfig,
    ManagedMCPToolPolicyConfig,
    ManagedSkillConfig,
)
from knoa_platform.extensions.mcp import (
    MCPClientPort,
    create_mcp_client,
    mcp_inventory_digest,
)
from knoa_platform.extensions.mcp_onboarding import (
    MCPInspectionResult,
    MCPOnboardingService,
)
from knoa_platform.extensions.mcp_package import load_mcp_package
from knoa_platform.extensions.models import MCPServerConfig
from knoa_platform.extensions.package_store import PackageRecord, PackageStore
from knoa_platform.extensions.skill import load_skill_package, skill_package_digest


class ConfigurationDraftPort(Protocol):
    async def create_config_draft(self, principal_id: str) -> ConfigDraft: ...

    async def replace_config_draft(
        self,
        principal_id: str,
        draft_id: str,
        document: ManagedConfig,
        *,
        expected_version: int,
    ) -> ConfigDraft: ...


@dataclass(frozen=True)
class ExtensionInspection:
    extension_id: str
    kind: str
    package_id: str
    inventory_digest: str
    tools: tuple[dict[str, object], ...]
    resources: tuple[dict[str, object], ...]
    prompts: tuple[dict[str, object], ...]
    requested_secrets: tuple[str, ...]
    withheld_tools: tuple[str, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "extension_id": self.extension_id,
            "kind": self.kind,
            "package_id": self.package_id,
            "inventory_digest": self.inventory_digest,
            "tools": list(self.tools),
            "resources": list(self.resources),
            "prompts": list(self.prompts),
            "requested_secrets": list(self.requested_secrets),
            "withheld_tools": list(self.withheld_tools),
        }


@dataclass(frozen=True)
class ExtensionImportResult:
    package: PackageRecord | None
    inspection: ExtensionInspection
    draft: ConfigDraft

    def as_dict(self) -> dict[str, object]:
        return {
            "package": None if self.package is None else self.package.public_dict(),
            "inspection": self.inspection.as_dict(),
            "draft": self.draft.model_dump(mode="json"),
        }


class ExtensionImportService:
    """Stage, inspect and write intent into Config; never activates providers itself."""

    def __init__(
        self,
        packages: PackageStore,
        configuration: ConfigurationDraftPort,
        *,
        mcp_inspector: MCPOnboardingService | None = None,
    ) -> None:
        self._packages = packages
        self._configuration = configuration
        self._mcp_inspector = mcp_inspector

    def list_packages(self) -> tuple[PackageRecord, ...]:
        return self._packages.list()

    async def import_skill(
        self,
        principal_id: str,
        source_path: str,
    ) -> ExtensionImportResult:
        source = Path(source_path).expanduser().resolve()
        candidate = load_skill_package(source)
        package = self._packages.import_directory(
            "skill",
            source,
            imported_by=principal_id,
        )
        frozen = load_skill_package(package.path)
        digest = skill_package_digest(frozen)
        document_draft = await self._configuration.create_config_draft(principal_id)
        document = document_draft.document
        skills = dict(document.skills)
        skills[frozen.manifest.id] = ManagedSkillConfig(
            package_id=package.package_id,
            source=str(package.path),
            enabled=True,
            content_digest=digest,
        )
        draft = await self._configuration.replace_config_draft(
            principal_id,
            document_draft.draft_id,
            document.model_copy(update={"skills": skills}),
            expected_version=document_draft.draft_version,
        )
        inspection = ExtensionInspection(
            extension_id=frozen.manifest.id,
            kind="skill",
            package_id=package.package_id,
            inventory_digest=digest,
            tools=(),
            resources=tuple(
                {"path": item.path, "size_chars": len(item.content)}
                for item in frozen.resources
            ),
            prompts=(),
            requested_secrets=(),
            withheld_tools=(),
        )
        del candidate
        return ExtensionImportResult(package=package, inspection=inspection, draft=draft)

    async def import_local_mcp(
        self,
        principal_id: str,
        source_path: str,
        server_id: str,
    ) -> ExtensionImportResult:
        source = Path(source_path).expanduser().resolve()
        load_mcp_package(source)
        package = self._packages.import_directory(
            "mcp",
            source,
            imported_by=principal_id,
        )
        connection = load_mcp_package(package.path)
        inspection = await self._inspect_mcp(server_id, connection, package.package_id)
        managed = self._managed_mcp(connection, inspection, package.package_id)
        draft = await self._draft_mcp(principal_id, server_id, managed)
        return ExtensionImportResult(package=package, inspection=inspection, draft=draft)

    async def import_remote_mcp(
        self,
        principal_id: str,
        server_id: str,
        url: str,
        *,
        allow_private_network: bool = False,
    ) -> ExtensionImportResult:
        normalized = self._validate_remote_url(url, allow_private_network=allow_private_network)
        connection = MCPServerConfig(
            enabled=True,
            transport="streamable_http",
            url=normalized,
        )
        inspection = await self._inspect_mcp(server_id, connection, "")
        managed = self._managed_mcp(connection, inspection, "")
        draft = await self._draft_mcp(principal_id, server_id, managed)
        return ExtensionImportResult(package=None, inspection=inspection, draft=draft)

    async def _draft_mcp(
        self,
        principal_id: str,
        server_id: str,
        managed: ManagedMCPConfig,
    ) -> ConfigDraft:
        draft = await self._configuration.create_config_draft(principal_id)
        servers = dict(draft.document.mcp_servers)
        servers[server_id] = managed
        return await self._configuration.replace_config_draft(
            principal_id,
            draft.draft_id,
            draft.document.model_copy(update={"mcp_servers": servers}),
            expected_version=draft.draft_version,
        )

    async def _inspect_mcp(
        self,
        server_id: str,
        connection: MCPServerConfig,
        package_id: str,
    ) -> ExtensionInspection:
        if self._mcp_inspector is not None:
            result = await self._mcp_inspector.inspect(connection)
        else:
            client = create_mcp_client(connection)
            try:
                await client.start()
                result = MCPInspectionResult(
                    tools=await client.list_tools(),
                    resources=(
                        await client.list_resources()
                        if client.resource_capabilities().available
                        else ()
                    ),
                    prompts=await self._optional_prompts(client),
                )
            finally:
                await client.close()
        digest = self.inventory_digest(result)
        tools = tuple(
            {
                "name": item.name,
                "description": item.description,
                "input_schema": item.input_schema,
                "read_only": item.read_only_hint,
                "destructive": item.destructive_hint,
                "idempotent": item.idempotent_hint,
                "open_world": item.open_world_hint,
            }
            for item in result.tools
        )
        withheld = tuple(
            sorted(item.name for item in result.tools if item.read_only_hint is not True)
        )
        return ExtensionInspection(
            extension_id=server_id,
            kind="mcp",
            package_id=package_id,
            inventory_digest=digest,
            tools=tools,
            resources=tuple(
                {
                    "uri": item.uri,
                    "name": item.name,
                    "description": item.description,
                    "mime_type": item.mime_type,
                }
                for item in result.resources
            ),
            prompts=tuple(
                {"name": item.name, "description": item.description}
                for item in result.prompts
            ),
            requested_secrets=tuple(sorted(connection.inherit_env)),
            withheld_tools=withheld,
        )

    @staticmethod
    def _managed_mcp(
        connection: MCPServerConfig,
        inspection: ExtensionInspection,
        package_id: str,
    ) -> ManagedMCPConfig:
        read_only = {
            str(tool["name"]): ManagedMCPToolPolicyConfig(
                effect="read_only",
                capabilities=frozenset(
                    {"mcp", "network"} if tool.get("open_world") is not False else {"mcp"}
                ),
                risk="low",
            )
            for tool in inspection.tools
            if tool.get("read_only") is True
        }
        return ManagedMCPConfig(
            transport=connection.transport,
            package_id=package_id,
            inventory_digest=inspection.inventory_digest,
            enabled=True,
            command=(),
            url=connection.url,
            working_directory="",
            inherit_env=connection.inherit_env,
            optional_env=connection.optional_env,
            timeout_seconds=connection.timeout_seconds,
            tools=read_only,
        )

    @staticmethod
    def inventory_digest(result: MCPInspectionResult) -> str:
        return mcp_inventory_digest(result.tools, result.resources, result.prompts)

    @staticmethod
    async def _optional_prompts(client: MCPClientPort) -> tuple:
        try:
            return await client.list_prompts()
        except Exception:  # noqa: BLE001 - prompts are an optional MCP capability
            return ()

    @staticmethod
    def _validate_remote_url(url: str, *, allow_private_network: bool) -> str:
        normalized = url.strip()
        parsed = urlparse(normalized)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("Remote MCP URL must use http or https")
        if parsed.username or parsed.password or parsed.fragment:
            raise ValueError("Remote MCP URL must not contain credentials or fragments")
        addresses = socket.getaddrinfo(parsed.hostname, parsed.port or 443, type=socket.SOCK_STREAM)
        for address in addresses:
            ip = ipaddress.ip_address(address[4][0])
            if not allow_private_network and not ip.is_global:
                raise ValueError("Remote MCP resolves to a private or local network address")
        return normalized


__all__ = [
    "ConfigurationDraftPort",
    "ExtensionImportResult",
    "ExtensionImportService",
    "ExtensionInspection",
]
