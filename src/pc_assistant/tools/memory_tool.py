from __future__ import annotations

from typing import Any

from pc_assistant.context.memory import EpisodicMemory, UserMemory
from pc_assistant.tools.base import ToolBase


class MemoryTool(ToolBase):
    name = "memory"
    description = "Store, retrieve, search, or delete user preferences and personal information for long-term memory"

    def __init__(
        self,
        memory: UserMemory | None = None,
        episodic: EpisodicMemory | None = None,
    ) -> None:
        self._memory = memory
        self._episodic = episodic

    def set_memory(self, memory: UserMemory) -> None:
        self._memory = memory

    def set_episodic(self, episodic: EpisodicMemory) -> None:
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
            "store_episode": self._store_episode,
            "recall_episodes": self._recall_episodes,
        }
        handler = handlers.get(action)
        if handler is None:
            return {"error": f"Unknown memory action: {action}. Use store/retrieve/search/delete."}
        return handler(kwargs)

    def _store(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        key = kwargs.get("key", "")
        value = kwargs.get("value", "")
        category = kwargs.get("category", "general")
        if not key or not value:
            return {"error": "Both 'key' and 'value' are required for store action"}
        self._memory.store(key, value, category=category, source="llm")
        return {"success": True, "key": key, "value": value, "category": category}

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

    def _store_episode(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        if self._episodic is None:
            return {"error": "Episodic memory not initialized"}
        summary = kwargs.get("value", "")
        if not summary:
            return {"error": "'value' (summary) is required for store_episode"}
        self._episodic.store_episode(summary)
        return {"success": True, "summary": summary}

    def _recall_episodes(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        if self._episodic is None:
            return {"error": "Episodic memory not initialized"}
        query = kwargs.get("key", "")
        episodes = self._episodic.recall(query, limit=5)
        return {"episodes": episodes, "count": len(episodes)}

    def schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["store", "retrieve", "search", "delete", "store_episode", "recall_episodes"],
                    },
                    "key": {
                        "type": "string",
                        "description": "Memory key (e.g. 'location', 'name', 'preference_editor')",
                    },
                    "value": {
                        "type": "string",
                        "description": "Value to store (required for store action)",
                    },
                    "category": {
                        "type": "string",
                        "description": "Category: identity, location, preference, workflow, instruction",
                    },
                },
                "required": ["action", "key"],
            },
        }

    def core_schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": "Long-term memory: store, retrieve, search, delete personal info.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["store", "retrieve", "search", "delete", "store_episode", "recall_episodes"]},
                    "key": {"type": "string"},
                    "value": {"type": "string"},
                    "category": {"type": "string"},
                },
                "required": ["action", "key"],
            },
        }
