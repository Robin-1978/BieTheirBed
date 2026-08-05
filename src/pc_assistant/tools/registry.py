from __future__ import annotations

import copy
from typing import Any

from pc_assistant.exceptions import ToolNotFoundError
from pc_assistant.tools.base import ToolBase


LLM_ACTION_NAMES: dict[str, dict[str, str]] = {}

LLM_PARAM_DESCRIPTIONS: dict[str, str] = {
    "action": "Operation.",
    "command": "Command.",
    "working_directory": "Run here.",
    "timeout_seconds": "Max seconds.",
    "file_path": "File or folder path.",
    "destination_path": "Copy/move target.",
    "launch_command": "App or launch command.",
    "app_name": "App name.",
    "element_name": "UI element name.",
    "application_name": "App or window name.",
    "text_to_type": "Text to type.",
    "window_id": "Window title or id.",
    "save_path": "Image save path.",
    "question": "Visible detail to read.",
    "image_id": "Image id.",
    "title": "Notification title.",
    "message": "Notification text.",
    "location": "Place name.",
    "forecast": "Current or forecast.",
    "query": "Search text.",
    "url": "Web address.",
    "max_results": "Result limit.",
}


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolBase] = {}

    def register(self, tool: ToolBase) -> None:
        if not tool.name:
            raise ValueError("Tool must have a non-empty name")
        self._tools[tool.name] = tool

    def get(self, name: str) -> ToolBase | None:
        return self._tools.get(self.resolve_name(name))

    def resolve_name(self, name: str) -> str:
        if name in self._tools:
            return name
        for internal, tool in self._tools.items():
            if getattr(tool, "llm_name", None) == name:
                return internal
        return name

    def normalize_call(self, name: str, arguments: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        internal = self.resolve_name(name)
        aliases = {
            item.internal_name: item.public_name
            for item in getattr(self._tools.get(internal), "llm_parameters", ())
        }
        reverse_aliases = {public: internal_key for internal_key, public in aliases.items()}
        normalized = {reverse_aliases.get(key, key): value for key, value in arguments.items()}
        action_aliases = LLM_ACTION_NAMES.get(internal, {})
        reverse_actions = {public: internal_action for internal_action, public in action_aliases.items()}
        if normalized.get("action") in reverse_actions:
            normalized["action"] = reverse_actions[normalized["action"]]
        return internal, normalized

    def llm_schema(self, name: str, *, skim: bool = False) -> dict[str, Any]:
        tool = self.get(name)
        if tool is None:
            return {}
        internal = tool.name
        raw = copy.deepcopy(tool.core_schema() if skim else tool.schema())
        raw["name"] = getattr(tool, "llm_name", None) or internal
        raw["description"] = (
            getattr(tool, "llm_skim_description", None)
            if skim else getattr(tool, "llm_description", None)
        ) or raw.get("description", tool.description)
        params = raw.get("parameters", {})
        aliases = {
            item.internal_name: item.public_name
            for item in getattr(tool, "llm_parameters", ())
        }
        properties = params.get("properties", {})
        params["properties"] = {aliases.get(key, key): value for key, value in properties.items()}
        params["required"] = [aliases.get(key, key) for key in params.get("required", [])]
        declared = {
            item.public_name: item
            for item in getattr(tool, "llm_parameters", ())
        }
        required = set(params.get("required", []))
        for key, item in declared.items():
            if item.required is True:
                required.add(key)
            elif item.required is False:
                required.discard(key)
            if item.description and key in params["properties"]:
                params["properties"][key]["description"] = item.description
        params["required"] = [key for key in params["properties"] if key in required]
        for key, prop in params["properties"].items():
            if key in LLM_PARAM_DESCRIPTIONS and isinstance(prop, dict):
                prop["description"] = LLM_PARAM_DESCRIPTIONS[key]
            if isinstance(prop, dict):
                required = key in set(params.get("required", []))
                prefix = "Required. " if required else "Optional. "
                desc = str(prop.get("description", "")).strip()
                if not desc.startswith(("Required. ", "Optional. ")):
                    prop["description"] = prefix + desc if desc else prefix.rstrip()
        action_aliases = LLM_ACTION_NAMES.get(internal, {})
        action_key = aliases.get("action", "action")
        action_prop = params["properties"].get(action_key)
        if action_prop and "enum" in action_prop:
            action_prop["enum"] = [action_aliases.get(value, value) for value in action_prop["enum"]]
            action_prop.setdefault("description", "Choose one exact operation from the enum.")
        if skim:
            required_names = set(params.get("required", []))
            skim_names = {
                item.public_name
                for item in getattr(tool, "llm_parameters", ())
                if item.skim
            }
            compact_properties: dict[str, Any] = {}
            for key, value in params.get("properties", {}).items():
                if key not in required_names and key not in skim_names:
                    continue
                compact: dict[str, Any] = {"type": value.get("type", "string")}
                if value.get("enum"):
                    compact["enum"] = value["enum"]
                declared_item = declared.get(key)
                hint = declared_item.skim_hint if declared_item is not None else ""
                if hint:
                    compact["description"] = hint[:120]
                compact_properties[key] = compact
            params["properties"] = compact_properties
            params.pop("additionalProperties", None)
        return raw

    def detailed_schema(self, name: str) -> dict[str, Any]:
        """Return the full schema plus human-readable examples for tool_help."""
        tool = self.get(name)
        if tool is None:
            return {}
        schema = self.llm_schema(name, skim=False)
        examples = list(getattr(tool, "llm_examples", []) or [])
        if not examples:
            properties = schema.get("parameters", {}).get("properties", {})
            example: dict[str, Any] = {}
            for key, prop in properties.items():
                if key == "action" and prop.get("enum"):
                    example[key] = prop["enum"][0]
                elif prop.get("type") == "boolean":
                    example[key] = False
                elif prop.get("type") == "integer":
                    example[key] = 1
                elif prop.get("type") == "number":
                    example[key] = 1
                elif prop.get("type") == "array":
                    example[key] = []
                else:
                    example[key] = "..."
            if example:
                examples = [example]
        return {
            "name": schema.get("name", tool.name),
            "description": schema.get("description", tool.description),
            "details": getattr(tool, "llm_details", "") or tool.description,
            "parameters": schema.get("parameters", {}),
            "examples": examples,
        }

    def list_tools(self) -> list[str]:
        return sorted(self._tools.keys())

    def list_llm_tools(self) -> list[str]:
        return sorted(getattr(tool, "llm_name", None) or name for name, tool in self._tools.items())

    def all_schemas(self) -> list[dict[str, Any]]:
        """Wrapped tool schemas for LLM API injection (single source: ``core_schema``)."""
        schemas: list[dict[str, Any]] = []
        for tool in self._tools.values():
            raw = self.llm_schema(tool.name, skim=True)
            schemas.append({
                "type": "function",
                "function": {
                    "name": raw["name"],
                    "description": raw.get("description", ""),
                    "parameters": raw.get("parameters", {"type": "object", "properties": {}}),
                },
            })
        return schemas

    def clear(self) -> None:
        """Unregister all tools."""
        self._tools.clear()

    async def _commit(self, internal_name: str, **kwargs: Any) -> Any:
        """Internal unchecked dispatch used only by ``VerifiedToolExecutor``.

        Model-proposed calls must never receive the registry as an execution
        capability. Discovery remains public; commit is an internal boundary.
        """
        # Do not name this positional parameter ``tool_name``: the describe
        # tool itself legitimately receives a model argument named
        # ``tool_name``.  With the old signature, dispatching
        # ``_commit("describe_tool", tool_name="tasks")`` raised Python's
        # "multiple values for argument" error before the tool could run.
        tool = self._tools.get(internal_name)
        if tool is None:
            raise ToolNotFoundError(internal_name)
        return await tool.execute(**kwargs)

    async def register_mcp_server(self, server_url: str) -> list[str]:
        """Discover and register all tools from an MCP server. Returns registered tool names."""
        from pc_assistant.tools.mcp_adapter import MCPToolAdapter

        adapter = MCPToolAdapter(server_url)
        tools = await adapter.discover()
        names: list[str] = []
        for tool in tools:
            if tool.name:
                self._tools[tool.name] = tool
                names.append(tool.name)
        return names

    def __contains__(self, name: str) -> bool:
        return name in self._tools

    def __len__(self) -> int:
        return len(self._tools)
