from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import Any

from jsonschema import Draft202012Validator


@dataclass(frozen=True)
class ToolParameter:
    """Metadata for a single tool parameter visible in skim schema."""

    name: str
    description: str = ""
    required: bool | None = None
    skim: bool = True


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


class ToolRisk(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


@dataclass(frozen=True)
class ToolPolicy:
    effect: ToolEffect
    capabilities: frozenset[ToolCapability]
    risk: ToolRisk

    @property
    def configured(self) -> bool:
        return self.effect is not ToolEffect.UNKNOWN


def parameter(
    name: str,
    *,
    description: str = "",
    required: bool | None = None,
    skim: bool = True,
):
    """Declare a parameter's skim-schema metadata on a tool class."""

    def decorate(cls):
        current = tuple(getattr(cls, "_declared_parameters", ()))
        cls._declared_parameters = current + (
            ToolParameter(
                name=name,
                description=description,
                required=required,
                skim=skim,
            ),
        )
        return cls

    return decorate


class ToolBase(ABC):
    name: str = ""
    description: str = ""
    effect: ToolEffect = ToolEffect.UNKNOWN
    capabilities: frozenset[ToolCapability] = frozenset()
    schema_capabilities: frozenset[ToolCapability] | None = None
    risk: ToolRisk = ToolRisk.HIGH
    _declared_parameters: tuple[ToolParameter, ...] = ()

    @abstractmethod
    async def execute(self, **kwargs: Any) -> Any: ...

    @abstractmethod
    def schema(self) -> dict[str, Any]:
        """Full JSON schema (returned by tool_help)."""

    def skim_schema(self) -> dict[str, Any]:
        """Compact schema for LLM injection. Override to omit rare params."""
        return self.schema()

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
        parameters = dict(self.schema().get("parameters") or {})
        parameters.setdefault("type", "object")
        parameters.setdefault("additionalProperties", False)
        Draft202012Validator.check_schema(parameters)
        return parameters

    def __repr__(self) -> str:
        return f"<Tool {self.name}>"
