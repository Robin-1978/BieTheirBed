from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pc_assistant.exceptions import ToolNotFoundError
from pc_assistant.tools.base import (
    BUILTIN_TOOL_ORIGIN,
    ToolBase,
    ToolCapability,
    ToolEffect,
    ToolOrigin,
    ToolPolicy,
    ToolRisk,
)


@dataclass(frozen=True)
class RegisteredToolDescriptor:
    name: str
    description: str
    origin: ToolOrigin
    policy: ToolPolicy
    requires_confirmation: bool


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolBase] = {}
        self._origins: dict[str, ToolOrigin] = {}

    def register(
        self,
        tool: ToolBase,
        *,
        origin: ToolOrigin = BUILTIN_TOOL_ORIGIN,
    ) -> None:
        if not tool.name:
            raise ValueError("Tool must have a non-empty name")
        if tool.name in self._tools:
            raise ValueError(f"Tool is already registered: {tool.name}")
        if tool.schema().get("name") != tool.name:
            raise ValueError(f"Tool schema name does not match: {tool.name}")
        if tool.skim_schema().get("name") != tool.name:
            raise ValueError(f"Tool skim schema name does not match: {tool.name}")
        tool.validation_schema()
        self._tools[tool.name] = tool
        self._origins[tool.name] = origin

    def unregister(self, name: str, *, origin: ToolOrigin) -> None:
        registered_origin = self._origins.get(name)
        if registered_origin is None:
            return
        if registered_origin != origin:
            raise PermissionError(f"Tool is owned by another origin: {name}")
        self._tools.pop(name, None)
        self._origins.pop(name, None)

    def get(self, name: str) -> ToolBase | None:
        return self._tools.get(name)

    def origin(self, name: str) -> ToolOrigin | None:
        return self._origins.get(name)

    def schemas_for(
        self,
        capabilities: frozenset[ToolCapability],
    ) -> list[dict[str, Any]]:
        """Return only configured tools allowed by the resolved runtime profile."""
        return [
            {
                "type": "function",
                "function": tool.skim_schema(),
            }
            for tool in self._tools.values()
            if (
                tool.policy.configured
                and tool.required_schema_capabilities <= capabilities
            )
        ]

    def policy(self, name: str) -> ToolPolicy | None:
        tool = self.get(name)
        return tool.policy if tool is not None else None

    def detailed_schema(self, name: str) -> dict[str, Any]:
        """Full schema plus examples for tool_help."""
        tool = self.get(name)
        if tool is None:
            return {}
        schema = tool.schema()
        examples = list(getattr(tool, "examples", []) or [])
        if not examples:
            properties = schema.get("parameters", {}).get("properties", {})
            required = set(schema.get("parameters", {}).get("required", []))
            example: dict[str, Any] = {}
            for key, prop in properties.items():
                if key not in required:
                    continue
                if prop.get("enum"):
                    example[key] = prop["enum"][0]
                elif prop.get("type") == "boolean":
                    example[key] = False
                elif prop.get("type") == "integer":
                    example[key] = 1
                elif prop.get("type") == "number":
                    example[key] = 1
                elif prop.get("type") == "array":
                    example[key] = []
                elif prop.get("type") == "object":
                    example[key] = {}
                else:
                    example[key] = "..."
            if example:
                examples = [example]
        return {
            "name": schema.get("name", tool.name),
            "description": schema.get("description", tool.description),
            "details": getattr(tool, "details", "") or tool.description,
            "parameters": schema.get("parameters", {}),
            "examples": examples,
        }

    def list_tools(self) -> list[str]:
        return sorted(
            name for name, tool in self._tools.items() if tool.policy.configured
        )

    def list_for(
        self,
        capabilities: frozenset[ToolCapability],
    ) -> list[str]:
        return sorted(
            name
            for name, tool in self._tools.items()
            if (
                tool.policy.configured
                and tool.required_schema_capabilities <= capabilities
            )
        )

    def descriptors_for(
        self,
        capabilities: frozenset[ToolCapability],
    ) -> tuple[RegisteredToolDescriptor, ...]:
        descriptors: list[RegisteredToolDescriptor] = []
        for name in self.list_for(capabilities):
            tool = self._tools[name]
            policy = tool.policy
            descriptors.append(
                RegisteredToolDescriptor(
                    name=name,
                    description=tool.description,
                    origin=self._origins[name],
                    policy=policy,
                    requires_confirmation=(
                        policy.effect is not ToolEffect.READ_ONLY
                        or policy.risk is ToolRisk.HIGH
                    ),
                )
            )
        return tuple(descriptors)

    def __contains__(self, name: str) -> bool:
        return name in self._tools

    def __len__(self) -> int:
        return len(self._tools)

    async def _commit(self, internal_name: str, **kwargs: Any) -> Any:
        """Internal unchecked dispatch used only by ToolStep."""
        tool = self._tools.get(internal_name)
        if tool is None:
            raise ToolNotFoundError(internal_name)
        return await tool.execute(**kwargs)
