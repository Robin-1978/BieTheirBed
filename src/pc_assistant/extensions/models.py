"""Strict local policy configuration for capability extensions."""
from __future__ import annotations

import re
from typing import Literal
from urllib.parse import urlparse

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    field_validator,
    model_validator,
)

from pc_assistant.tools.base import ToolCapability, ToolEffect, ToolRisk


MCP_SERVER_ID_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,23}$")
MCP_TOOL_NAME_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,31}$")
CONNECTOR_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{0,23}$")
SECRET_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")


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


class MCPServerConfig(ExtensionConfigModel):
    enabled: bool = False
    transport: Literal["streamable_http"] = "streamable_http"
    url: str = ""
    timeout_seconds: float = Field(default=30.0, ge=1.0, le=300.0)
    tools: dict[str, MCPToolPolicyConfig] = Field(default_factory=dict)

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

    @model_validator(mode="after")
    def validate_enabled_server(self) -> MCPServerConfig:
        if self.enabled and not self.url:
            raise ValueError("Enabled MCP server requires a URL")
        invalid = [name for name in self.tools if not MCP_TOOL_NAME_PATTERN.fullmatch(name)]
        if invalid:
            raise ValueError("MCP tool names must contain 1-32 safe characters")
        return self


class YuqueConnectorConfig(ExtensionConfigModel):
    enabled: bool = False
    driver: Literal["yuque"] = "yuque"
    base_url: str = "https://www.yuque.com/api/v2"
    token_secret: SecretStr = Field(default_factory=lambda: SecretStr(""))
    timeout_seconds: float = Field(default=30.0, ge=1.0, le=300.0)

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, value: str) -> str:
        normalized = value.strip().rstrip("/")
        parsed = urlparse(normalized)
        if parsed.scheme != "https" or not parsed.hostname:
            raise ValueError("Yuque Connector base_url must use https")
        if parsed.username or parsed.password:
            raise ValueError("Connector URL must not contain credentials")
        if parsed.query or parsed.fragment:
            raise ValueError("Connector base_url must not contain query or fragment")
        return normalized

    @field_validator("token_secret")
    @classmethod
    def validate_token_secret(cls, value: SecretStr) -> SecretStr:
        normalized = value.get_secret_value().strip()
        if normalized and not SECRET_ID_PATTERN.fullmatch(normalized):
            raise ValueError("Connector token_secret must be a safe Secret ID")
        return SecretStr(normalized)

    @model_validator(mode="after")
    def validate_enabled_connector(self) -> YuqueConnectorConfig:
        if self.enabled and not self.token_secret.get_secret_value():
            raise ValueError("Enabled Yuque Connector requires token_secret")
        return self
