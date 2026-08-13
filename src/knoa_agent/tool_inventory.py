"""Standard MCP inventory caching and Agent-private model projection."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any


_MODEL_SCHEMA_KEYS = frozenset(
    {
        "type",
        "properties",
        "required",
        "enum",
        "const",
        "items",
        "prefixItems",
        "oneOf",
        "anyOf",
        "allOf",
        "$ref",
        "$defs",
    }
)


@dataclass(frozen=True)
class ToolInventorySnapshot:
    """Complete, normalized standard MCP tools visible to one grant."""

    tools: tuple[dict[str, Any], ...]
    schema_chars: int


class ToolInventory:
    """Keep MCP discovery complete while bounding what each model call sees.

    Ordinary conversation tools are a stable static prefix. Platform MCP
    management tools and proxied upstream MCP tools use the reserved ``mcp_``
    prefix and are activated for a Runtime Session after discovery through
    ``tool_help``.
    """

    def __init__(self, *, schema_char_budget: int = 24_000) -> None:
        if schema_char_budget < 1000:
            raise ValueError("Tool schema budget must be at least 1000 characters")
        self._schema_char_budget = schema_char_budget
        self._cache: dict[tuple[str, str], ToolInventorySnapshot] = {}
        self._active_deferred: dict[str, set[str]] = {}

    async def load(
        self,
        runtime_session_ref: str,
        scope_digest: str,
        client: Any,
    ) -> ToolInventorySnapshot:
        key = (runtime_session_ref, scope_digest)
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        listed = await client.list_tools()
        normalized = tuple(
            sorted(
                (self._normalize(tool) for tool in listed),
                key=lambda tool: str(tool["name"]),
            )
        )
        snapshot = ToolInventorySnapshot(
            tools=normalized,
            schema_chars=sum(self._serialized_size(tool) for tool in normalized),
        )
        self._cache[key] = snapshot
        available = {str(tool["name"]) for tool in normalized}
        active = self._active_deferred.get(runtime_session_ref)
        if active is not None:
            active.intersection_update(available)
        return snapshot

    def project(
        self,
        runtime_session_ref: str,
        snapshot: ToolInventorySnapshot,
    ) -> tuple[dict[str, Any], ...]:
        """Return stable built-ins plus session-activated deferred tools."""

        active = self._active_deferred.get(runtime_session_ref, set())
        projected = tuple(
            self._model_signature(tool)
            for tool in snapshot.tools
            if not self._is_deferred(str(tool["name"]))
            or str(tool["name"]) in active
        )
        projected_chars = sum(self._serialized_size(tool) for tool in projected)
        if projected_chars > self._schema_char_budget:
            raise ValueError(
                "Selected model tool signatures exceed the configured budget"
            )
        return projected

    def activate(
        self,
        runtime_session_ref: str,
        snapshot: ToolInventorySnapshot,
        names: set[str] | frozenset[str],
    ) -> tuple[str, ...]:
        """Activate discovered deferred tools for later model steps."""

        available = {str(tool["name"]) for tool in snapshot.tools}
        selected = {
            name
            for name in names
            if name in available and self._is_deferred(name)
        }
        if not selected:
            return ()
        active = self._active_deferred.setdefault(runtime_session_ref, set())
        active.update(selected)
        return tuple(sorted(selected))

    def invalidate_session(self, runtime_session_ref: str) -> None:
        for key in tuple(self._cache):
            if key[0] == runtime_session_ref:
                self._cache.pop(key, None)
        self._active_deferred.pop(runtime_session_ref, None)

    @staticmethod
    def _is_deferred(name: str) -> bool:
        return name.startswith("mcp_")

    @classmethod
    def _model_signature(cls, tool: dict[str, Any]) -> dict[str, Any]:
        """Project a full MCP Tool into a compact provider-call signature."""
        name = str(tool["name"])
        input_schema = cls._project_schema(tool["inputSchema"])
        cls._apply_tool_specific_skim(name, input_schema)
        return {
            "name": name,
            "inputSchema": input_schema,
        }

    @staticmethod
    def _apply_tool_specific_skim(name: str, schema: dict[str, Any]) -> None:
        """Keep uncommon complex parameters behind standard tool_help."""

        properties = schema.get("properties")
        if not isinstance(properties, dict):
            return
        if name == "task":
            core = {
                key: properties[key]
                for key in ("action", "task_id", "execution_id")
                if key in properties
            }
            schema["properties"] = core
            return
        if name != "create_task":
            return
        launch = properties.get("launch")
        if not isinstance(launch, dict):
            return
        launch_properties = launch.get("properties")
        if not isinstance(launch_properties, dict):
            return
        kind = launch_properties.get("kind")
        launch["properties"] = {"kind": kind} if isinstance(kind, dict) else {}
        launch["required"] = ["kind"]

    @classmethod
    def _project_schema(cls, value: Any) -> Any:
        if isinstance(value, list):
            return [cls._project_schema(item) for item in value]
        if not isinstance(value, dict):
            return value
        projected: dict[str, Any] = {}
        for key, item in value.items():
            if key not in _MODEL_SCHEMA_KEYS:
                continue
            if key in {"properties", "$defs"} and isinstance(item, dict):
                projected[key] = {
                    str(name): cls._project_schema(schema)
                    for name, schema in item.items()
                }
            else:
                projected[key] = cls._project_schema(item)
        if projected.get("type") == "object":
            projected.setdefault("properties", {})
        return projected

    @staticmethod
    def _normalize(tool: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(tool, dict):
            raise ValueError("MCP Tool definition must be an object")
        name = str(tool.get("name") or "").strip()
        schema = tool.get("inputSchema")
        if not name or not isinstance(schema, dict):
            raise ValueError("MCP Tool definition requires name and inputSchema")
        normalized = {
            "name": name,
            "description": str(tool.get("description") or ""),
            "inputSchema": json.loads(
                json.dumps(schema, ensure_ascii=False, sort_keys=True)
            ),
        }
        output_schema = tool.get("outputSchema")
        if isinstance(output_schema, dict):
            normalized["outputSchema"] = json.loads(
                json.dumps(output_schema, ensure_ascii=False, sort_keys=True)
            )
        return normalized

    @staticmethod
    def _serialized_size(tool: dict[str, Any]) -> int:
        return len(
            json.dumps(
                tool,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        )
