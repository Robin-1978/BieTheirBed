from __future__ import annotations

from typing import Any, Protocol

from pc_assistant.context.memory_db import MemoryItem
from pc_assistant.tools.base import (
    ToolBase,
    ToolCapability,
    ToolEffect,
    ToolPolicy,
    ToolRisk,
)


class UserMemoryPort(Protocol):
    def store(
        self,
        key: str,
        value: str,
        category: str = "general",
        confidence: float = 1.0,
        source: str = "explicit",
        importance: str | None = None,
    ) -> None: ...

    def retrieve(self, key: str) -> MemoryItem | None: ...
    def search(self, query: str, limit: int = 5) -> list[MemoryItem]: ...
    def delete(self, key: str) -> bool: ...


class EpisodicMemoryPort(Protocol):
    def store_episode(self, summary: str, session_id: str = "", tool_calls: int = 0) -> None: ...
    def recall(self, query: str = "", limit: int = 5) -> list[dict[str, Any]]: ...


class MemoryTool(ToolBase):
    name = "memory"
    description = "Store, retrieve, search, or delete user facts."
    effect = ToolEffect.LOCAL_WRITE
    capabilities = frozenset({ToolCapability.MEMORY_WRITE})
    schema_capabilities = frozenset({ToolCapability.MEMORY_READ})
    risk = ToolRisk.MEDIUM

    def policy_for(self, arguments: dict[str, Any]) -> ToolPolicy:
        if arguments.get("action") in {"retrieve", "search"}:
            return ToolPolicy(
                effect=ToolEffect.READ_ONLY,
                capabilities=frozenset({ToolCapability.MEMORY_READ}),
                risk=ToolRisk.LOW,
            )
        return self.policy

    def __init__(
        self,
        memory: UserMemoryPort | None = None,
        episodic: EpisodicMemoryPort | None = None,
    ) -> None:
        self._memory = memory
        self._episodic = episodic

    def set_memory(self, memory: UserMemoryPort) -> None:
        self._memory = memory

    def set_episodic(self, episodic: EpisodicMemoryPort) -> None:
        self._episodic = episodic

    async def execute(self, **kwargs: Any) -> Any:
        if self._memory is None:
            return {"error": "Memory not initialized"}
        action = kwargs.get("action")
        handlers = {
            "store": self._store,
            "retrieve": self._retrieve,
            "search": self._search,
            "delete": self._delete,
        }
        handler = handlers.get(action)
        if handler is None:
            return {"error": f"Unknown memory action: {action}. Use store/retrieve/search/delete."}
        return handler(kwargs)

    def _store(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        key = kwargs.get("key", "")
        value = kwargs.get("value", "")
        category = kwargs.get("category", "general")
        importance = kwargs.get("importance")
        if not key or not value:
            return {"error": "Both 'key' and 'value' are required for store action"}
        try:
            self._memory.store(
                key,
                value,
                category=category,
                source="explicit-tool",
                importance=importance,
            )
        except ValueError as exc:
            return {"error": str(exc)}
        return {
            "success": True,
            "key": key,
            "value": value,
            "category": category,
            "importance": importance or "policy-default",
        }

    def _retrieve(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        key = kwargs.get("key", "")
        if not key:
            return {"error": "'key' is required for retrieve action"}
        item = self._memory.retrieve(key)
        if item is None:
            return {"found": False, "key": key}
        return {"found": True, "key": item.key, "value": item.value, "category": item.category}

    def _search(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        query = kwargs.get("key", "")
        if not query:
            return {"error": "'key' (search query) is required for search action"}
        results = self._memory.search(query, limit=5)
        return {
            "results": [
                {"key": r.key, "value": r.value, "category": r.category}
                for r in results
            ],
            "count": len(results),
        }

    def _delete(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        key = kwargs.get("key", "")
        if not key:
            return {"error": "'key' is required for delete action"}
        deleted = self._memory.delete(key)
        return {"deleted": deleted, "key": key}

    def schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["store", "retrieve", "search", "delete"],
                    },
                    "key": {
                        "type": "string",
                        "description": "Explicit snake_case key naming its subject, e.g. user_name, assistant_name, preferred_language. Ambiguous keys such as name are rejected.",
                    },
                    "value": {
                        "type": "string",
                        "description": "Value to store (required for store action)",
                    },
                    "category": {
                        "type": "string",
                        "description": "Category: identity, communication, preference, workflow, safety, environment, instruction",
                    },
                    "importance": {
                        "type": "string",
                        "enum": ["core", "relevant"],
                        "description": "core is small, always injected, and requires user confirmation; relevant is retrieved only for matching requests",
                    },
                },
                "required": ["action"],
            },
        }

    def skim_schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["store", "retrieve", "search", "delete"]},
                    "key": {"type": "string", "description": "snake_case fact key, not name"},
                    "value": {"type": "string", "description": "for store"},
                    "category": {"type": "string"},
                    "importance": {"type": "string", "enum": ["core", "relevant"]},
                },
                "required": ["action"],
            },
        }
