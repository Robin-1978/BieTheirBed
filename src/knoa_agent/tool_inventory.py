"""Standard MCP inventory caching and Agent-private model projection."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from knoa_agent.tool_selector import SemanticSelection, default_tool_selector

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


@dataclass(frozen=True)
class ToolProjection:
    """Model-visible tools plus bounded selection observability."""

    tools: tuple[dict[str, Any], ...]
    mode: str
    matched_names: tuple[str, ...]
    schema_hits: int


class ToolInventory:
    """Keep MCP discovery complete while bounding what each model call sees.

    Ordinary conversation tools are a stable static prefix. Platform MCP
    management tools and proxied upstream MCP tools use the reserved ``mcp_``
    prefix and are activated for a Runtime Session after discovery through
    ``tool_help``.
    """

    def __init__(
        self,
        *,
        schema_char_budget: int = 24_000,
        semantic_selector: Any | None = None,
    ) -> None:
        if schema_char_budget < 1000:
            raise ValueError("Tool schema budget must be at least 1000 characters")
        self._schema_char_budget = schema_char_budget
        self._cache: dict[tuple[str, str], ToolInventorySnapshot] = {}
        self._active_deferred: dict[str, set[str]] = {}
        self._semantic_selector = semantic_selector or default_tool_selector()

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

    async def project_for_turn(
        self,
        runtime_session_ref: str,
        snapshot: ToolInventorySnapshot,
        query: str,
    ) -> ToolProjection:
        """Recall relevant deferred tools before the first model step.

        A Resource Task's standard ``MCP server: <id>`` envelope deterministically
        selects the matching namespace.  Ordinary turns use lexical recall OR an
        optional local BGE match.  Existing session activations remain visible.
        """

        deferred = tuple(
            tool for tool in snapshot.tools if self._is_deferred(str(tool["name"]))
        )
        source_names = self._source_namespace_matches(query, deferred)
        lexical_names = (
            frozenset() if source_names else self._lexical_matches(query, deferred)
        )
        semantic = SemanticSelection()
        if deferred and query.strip() and not source_names:
            start_loading = getattr(self._semantic_selector, "start_loading", None)
            if callable(start_loading):
                start_loading()
            candidates = tuple(
                (str(tool["name"]), str(tool.get("description") or ""))
                for tool in deferred
            )
            semantic = self._semantic_selector.select(query, candidates)
        recalled = frozenset({*source_names, *lexical_names, *semantic.names})
        self.activate(runtime_session_ref, snapshot, recalled)
        active = self._active_deferred.get(runtime_session_ref, set())
        tools = self.project(runtime_session_ref, snapshot)
        modes = []
        if source_names:
            modes.append("source")
        if lexical_names:
            modes.append("lexical")
        if semantic.names:
            modes.append(semantic.mode)
        if not modes:
            modes.append("static")
        return ToolProjection(
            tools=tools,
            mode="+".join(modes),
            matched_names=tuple(sorted(recalled)),
            schema_hits=len(active),
        )

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

    @staticmethod
    def _source_namespace_matches(
        query: str,
        deferred: tuple[dict[str, Any], ...],
    ) -> frozenset[str]:
        match = re.search(r"(?im)^MCP server:\s*([A-Za-z0-9_.-]+)\s*$", query)
        if match is None:
            return frozenset()
        prefix = f"mcp__{match.group(1).casefold()}__"
        return frozenset(
            str(tool["name"])
            for tool in deferred
            if str(tool["name"]).casefold().startswith(prefix)
        )

    @classmethod
    def _lexical_matches(
        cls,
        query: str,
        deferred: tuple[dict[str, Any], ...],
    ) -> frozenset[str]:
        tokens = cls._tokens(query)
        if not tokens:
            return frozenset()
        ranked: list[tuple[int, str]] = []
        for tool in deferred:
            name = str(tool["name"])
            description = str(tool.get("description") or "")
            searchable = cls._tokens(f"{name} {description}")
            score = len(tokens & searchable)
            if score:
                ranked.append((score, name))
        ranked.sort(key=lambda item: (-item[0], item[1]))
        return frozenset(name for _score, name in ranked[:6])

    @staticmethod
    def _tokens(value: str) -> frozenset[str]:
        return frozenset(
            token
            for token in re.findall(r"[A-Za-z0-9]+|[\u4e00-\u9fff]{2,}", value.casefold())
            if len(token) >= 2
        )

    @classmethod
    def _model_signature(cls, tool: dict[str, Any]) -> dict[str, Any]:
        """Project a full MCP Tool into a compact provider-call signature."""
        name = str(tool["name"])
        input_schema = cls._project_schema(tool["inputSchema"])
        cls._apply_tool_specific_skim(name, input_schema)
        return {
            "name": name,
            "description": str(tool.get("description") or "")[:240],
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
            raise TypeError("MCP Tool definition must be an object")
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
