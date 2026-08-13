from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from jsonschema import Draft202012Validator

from knoa_platform.exceptions import ToolNotFoundError
from knoa_platform.tools.base import (
    BUILTIN_TOOL_ORIGIN,
    ToolBase,
    ToolCapability,
    ToolOrigin,
    ToolPolicy,
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
        self._canonical_definition(tool, tool.definition(), label="full")
        self._canonical_definition(tool, tool.skim_definition(), label="skim")
        self._tools[tool.name] = tool
        self._origins[tool.name] = origin

    @staticmethod
    def _canonical_definition(
        tool: ToolBase,
        definition: dict[str, Any],
        *,
        label: str,
    ) -> dict[str, Any]:
        allowed = {"name", "description", "inputSchema", "outputSchema"}
        unexpected = set(definition) - allowed
        if unexpected:
            raise ValueError(
                f"Tool {label} definition contains unsupported MCP fields: "
                f"{sorted(unexpected)}"
            )
        if definition.get("name") != tool.name:
            raise ValueError(f"Tool {label} definition name does not match: {tool.name}")
        description = definition.get("description", "")
        if not isinstance(description, str):
            raise ValueError("Tool description must be text")
        input_schema = definition.get("inputSchema")
        if not isinstance(input_schema, dict):
            raise ValueError("Tool definition requires an MCP inputSchema object")
        normalized_input = dict(input_schema)
        normalized_input.setdefault("type", "object")
        normalized_input.setdefault("additionalProperties", False)
        if normalized_input.get("type") != "object":
            raise ValueError("Tool inputSchema must describe an object")
        Draft202012Validator.check_schema(normalized_input)
        output_schema = definition.get("outputSchema")
        if output_schema is not None:
            if not isinstance(output_schema, dict):
                raise ValueError("Tool outputSchema must be an object")
            Draft202012Validator.check_schema(output_schema)
        normalized = {
            "name": tool.name,
            "description": (
                description
                if label == "skim"
                else description or tool.description
            ),
            "inputSchema": normalized_input,
        }
        if output_schema is not None:
            normalized["outputSchema"] = dict(output_schema)
        return normalized

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

    def definitions_for(
        self,
        capabilities: frozenset[ToolCapability],
    ) -> list[dict[str, Any]]:
        """Return complete authorized MCP Tool definitions for tools/list."""
        return [
            self._canonical_definition(
                tool,
                tool.definition(),
                label="full",
            )
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
        definition = self._canonical_definition(
            tool,
            tool.definition(),
            label="full",
        )
        examples = list(getattr(tool, "examples", []) or [])
        if not examples:
            properties = definition.get("inputSchema", {}).get("properties", {})
            required = set(definition.get("inputSchema", {}).get("required", []))
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
        detail = {
            "name": definition.get("name", tool.name),
            "description": definition.get("description", tool.description),
            "details": getattr(tool, "details", "") or tool.description,
            "inputSchema": definition.get("inputSchema", {}),
            "examples": examples,
        }
        if "outputSchema" in definition:
            detail["outputSchema"] = definition["outputSchema"]
        return detail

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
                    requires_confirmation=policy.requires_confirmation,
                )
            )
        return tuple(descriptors)

    def __contains__(self, name: str) -> bool:
        return name in self._tools

    def __len__(self) -> int:
        return len(self._tools)

    async def _commit(
        self,
        internal_name: str,
        *,
        scope: Any = None,
        **kwargs: Any,
    ) -> Any:
        """Internal unchecked dispatch used only by ToolStep."""
        tool = self._tools.get(internal_name)
        if tool is None:
            raise ToolNotFoundError(internal_name)
        return await tool.execute_scoped(scope, **kwargs)
