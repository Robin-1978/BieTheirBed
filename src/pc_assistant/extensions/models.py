"""Strict local policy configuration for capability extensions."""

from __future__ import annotations

import re
from typing import Literal
from urllib.parse import unquote, urlparse

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from pc_assistant.tools.base import ToolCapability, ToolEffect, ToolRisk

MCP_SERVER_ID_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,23}$")
MCP_ROUTE_ID_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
MCP_TOOL_NAME_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,127}$")
ENVIRONMENT_NAME_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
INVALID_PERCENT_PATTERN = re.compile(r"%(?![0-9A-Fa-f]{2})")


class ExtensionConfigModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        hide_input_in_errors=True,
    )


class MCPToolPolicyConfig(ExtensionConfigModel):
    effect: ToolEffect
    capabilities: frozenset[ToolCapability] = frozenset()
    risk: ToolRisk

    @model_validator(mode="after")
    def reject_unknown_effect(self) -> MCPToolPolicyConfig:
        if self.effect is ToolEffect.UNKNOWN:
            raise ValueError("MCP tool effect must be explicitly configured")
        return self


class MCPResourceTaskConfig(ExtensionConfigModel):
    """One explicitly trusted MCP Resource scope routed into durable Tasks."""

    uri: str
    principal_id: str = Field(min_length=1, max_length=256)
    session_handle: str = Field(min_length=1, max_length=256)
    include_root: bool = False
    tools_enabled: bool = True
    priority: int = Field(default=0, ge=0, le=9)

    @field_validator("uri")
    @classmethod
    def validate_uri(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized or len(normalized) > 4096 or "\x00" in normalized:
            raise ValueError(
                "MCP Resource Task URI must contain 1-4096 safe characters"
            )
        parsed = urlparse(normalized)
        if not parsed.scheme:
            raise ValueError("MCP Resource Task URI must be absolute")
        if parsed.username or parsed.password or parsed.fragment:
            raise ValueError(
                "MCP Resource Task URI must not contain credentials or a fragment"
            )
        if parsed.query or INVALID_PERCENT_PATTERN.search(normalized):
            raise ValueError(
                "MCP Resource Task URI must not contain a query or invalid encoding"
            )
        for encoded in parsed.path.split("/"):
            decoded = unquote(encoded)
            if (
                decoded in {".", ".."}
                or "/" in decoded
                or "\\" in decoded
                or any(
                    ord(character) < 0x20 or ord(character) == 0x7F
                    for character in decoded
                )
            ):
                raise ValueError("MCP Resource Task URI contains an unsafe path")
        return normalized


class MCPServerConfig(ExtensionConfigModel):
    enabled: bool = False
    transport: Literal["streamable_http", "stdio"] = "streamable_http"
    url: str = ""
    command: str = ""
    args: tuple[str, ...] = Field(default=(), max_length=64)
    working_directory: str = ""
    inherit_env: tuple[str, ...] = Field(default=(), max_length=64)
    timeout_seconds: float = Field(default=30.0, ge=1.0, le=300.0)
    tools: dict[str, MCPToolPolicyConfig] = Field(default_factory=dict)
    resource_tasks: dict[str, MCPResourceTaskConfig] = Field(default_factory=dict)

    @field_validator("url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            return normalized
        parsed = urlparse(normalized)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("MCP Streamable HTTP URL must use http or https")
        if parsed.username or parsed.password:
            raise ValueError("MCP URL must not contain credentials")
        return normalized

    @field_validator("command", "working_directory")
    @classmethod
    def validate_process_string(cls, value: str) -> str:
        normalized = value.strip()
        if len(normalized) > 4096 or "\x00" in normalized:
            raise ValueError(
                "MCP process fields must contain at most 4096 safe characters"
            )
        return normalized

    @field_validator("args")
    @classmethod
    def validate_args(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if any(len(value) > 4096 or "\x00" in value for value in values):
            raise ValueError("MCP arguments must contain at most 4096 safe characters")
        return values

    @field_validator("inherit_env")
    @classmethod
    def validate_inherit_env(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(value.strip() for value in values)
        if any(not ENVIRONMENT_NAME_PATTERN.fullmatch(value) for value in normalized):
            raise ValueError("MCP inherited environment names must be safe identifiers")
        if len(set(normalized)) != len(normalized):
            raise ValueError("MCP inherited environment names must be unique")
        return normalized

    @model_validator(mode="after")
    def validate_enabled_server(self) -> MCPServerConfig:
        if self.transport == "streamable_http":
            if self.enabled and not self.url:
                raise ValueError("Enabled Streamable HTTP MCP server requires a URL")
            if self.command or self.args or self.working_directory or self.inherit_env:
                raise ValueError(
                    "Streamable HTTP MCP server must not configure stdio fields"
                )
        else:
            if self.enabled and not self.command:
                raise ValueError("Enabled stdio MCP server requires a command")
            if self.url:
                raise ValueError("stdio MCP server must not configure a URL")
        invalid = [
            name for name in self.tools if not MCP_TOOL_NAME_PATTERN.fullmatch(name)
        ]
        if invalid:
            raise ValueError("MCP tool names must contain 1-128 safe characters")
        invalid_routes = [
            route_id
            for route_id in self.resource_tasks
            if not MCP_ROUTE_ID_PATTERN.fullmatch(route_id)
        ]
        if invalid_routes:
            raise ValueError("MCP Resource Task IDs must contain 1-64 safe characters")
        return self
