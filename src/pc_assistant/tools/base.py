from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import Any, TypedDict

from jsonschema import Draft202012Validator


class _RequiredToolDefinition(TypedDict):
    """Required fields from the MCP Tool definition contract."""

    name: str
    inputSchema: dict[str, Any]


class ToolDefinition(_RequiredToolDefinition, total=False):
    """Knoa's MCP-compatible canonical Tool definition."""

    description: str
    outputSchema: dict[str, Any]


class ToolEffect(str, Enum):
    READ_ONLY = "read_only"
    LOCAL_WRITE = "local_write"
    EXTERNAL_SIDE_EFFECT = "external_side_effect"
    DESKTOP_CONTROL = "desktop_control"
    UNKNOWN = "unknown"


class ToolCapability(str, Enum):
    HOST_READ = "host_read"
    HOST_WRITE = "host_write"
    SHELL = "shell"
    NETWORK = "network"
    DESKTOP_OBSERVE = "desktop_observe"
    DESKTOP_CONTROL = "desktop_control"
    MEMORY_READ = "memory_read"
    MEMORY_WRITE = "memory_write"
    MCP = "mcp"


class ToolRisk(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ToolOriginKind(str, Enum):
    BUILTIN = "builtin"
    MCP = "mcp"


@dataclass(frozen=True)
class ToolOrigin:
    kind: ToolOriginKind
    extension_id: str

    def __post_init__(self) -> None:
        normalized = self.extension_id.strip()
        if not normalized or len(normalized) > 128:
            raise ValueError("Tool origin extension_id must contain 1-128 characters")
        object.__setattr__(self, "extension_id", normalized)


BUILTIN_TOOL_ORIGIN = ToolOrigin(
    kind=ToolOriginKind.BUILTIN,
    extension_id="builtin",
)


@dataclass(frozen=True)
class ToolPolicy:
    effect: ToolEffect
    capabilities: frozenset[ToolCapability]
    risk: ToolRisk

    @property
    def configured(self) -> bool:
        return self.effect is not ToolEffect.UNKNOWN


class ToolBase(ABC):
    name: str = ""
    description: str = ""
    effect: ToolEffect = ToolEffect.UNKNOWN
    capabilities: frozenset[ToolCapability] = frozenset()
    schema_capabilities: frozenset[ToolCapability] | None = None
    risk: ToolRisk = ToolRisk.HIGH
    @abstractmethod
    async def execute(self, **kwargs: Any) -> Any: ...

    @abstractmethod
    def definition(self) -> ToolDefinition:
        """Return the full MCP-compatible canonical Tool definition."""

    def skim_definition(self) -> ToolDefinition:
        """Return a compact canonical definition for model injection."""
        return self.definition()

    @property
    def policy(self) -> ToolPolicy:
        return ToolPolicy(
            effect=self.effect,
            capabilities=self.capabilities,
            risk=self.risk,
        )

    @property
    def required_schema_capabilities(self) -> frozenset[ToolCapability]:
        if self.schema_capabilities is not None:
            return self.schema_capabilities
        return self.capabilities

    def policy_for(self, arguments: dict[str, Any]) -> ToolPolicy:
        """Resolve call-specific effect/risk; mixed-action tools may override."""
        del arguments
        return self.policy

    def validation_schema(self) -> dict[str, Any]:
        """Return the fail-closed JSON Schema used at the commit boundary."""
        input_schema = dict(self.definition().get("inputSchema") or {})
        input_schema.setdefault("type", "object")
        input_schema.setdefault("additionalProperties", False)
        Draft202012Validator.check_schema(input_schema)
        return input_schema

    def __repr__(self) -> str:
        return f"<Tool {self.name}>"
