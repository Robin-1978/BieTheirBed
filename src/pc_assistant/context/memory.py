from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from pc_assistant.logger import get_logger


class MemoryItem:
    def __init__(self, key: str, value: str, category: str = "general", confidence: float = 1.0, source: str = "conversation") -> None:
        self.key = key
        self.value = value
        self.category = category
        self.confidence = min(1.0, max(0.0, confidence))
        self.source = source
        self.created_at = datetime.now().isoformat()
        self.updated_at = datetime.now().isoformat()
        self.access_count = 0

    def touch(self) -> None:
        self.access_count += 1
        self.updated_at = datetime.now().isoformat()

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "value": self.value,
            "category": self.category,
            "confidence": self.confidence,
            "source": self.source,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "access_count": self.access_count,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MemoryItem:
        item = cls(
            key=data["key"],
            value=data["value"],
            category=data.get("category", "general"),
            confidence=data.get("confidence", 1.0),
            source=data.get("source", "conversation"),
        )
        item.created_at = data.get("created_at", item.created_at)
        item.updated_at = data.get("updated_at", item.updated_at)
        item.access_count = data.get("access_count", 0)
        return item


_PREFERENCE_PATTERNS: list[tuple[str, str, str]] = [
    (r"(?:i (?:prefer|like|love|enjoy|always|usually|often|normally))\s+(.+?)(?:\.|!|$)", "preference", "conversation"),
    (r"(?:i (?:don'?t|do not|never|rarely|hate|dislike))\s+(.+?)(?:\.|!|$)", "preference", "conversation"),
    (r"(?:my (?:name|username|nickname)\s+(?:is|:)\s+)(.+?)(?:\.|!|$)", "identity", "conversation"),
    (r"(?:i (?:am|work as|am a|am an)\s+(?:a\s+)?)(.+?)(?:\.|!|$)", "identity", "conversation"),
    (r"(?:i (?:live|am based|reside)\s+(?:in|at)\s+)(.+?)(?:\.|!|$)", "location", "conversation"),
    (r"(?:my (?:favorite|fav|preferred)\s+\w+\s+(?:is|are|:)\s+)(.+?)(?:\.|!|$)", "preference", "conversation"),
    (r"(?:i (?:use|work with|develop in|code in)\s+)(.+?)(?:\.|!|$)", "workflow", "conversation"),
    (r"(?:my (?:project|repo|repository|codebase)\s+(?:is|:)\s+)(.+?)(?:\.|!|$)", "workflow", "conversation"),
    (r"(?:i (?:speak|know)\s+)(.+?)(?:\.|!|$)", "identity", "conversation"),
    (r"(?:please?\s+(?:always|never|remember\s+to))\s+(.+?)(?:\.|!|$)", "instruction", "conversation"),
]


class UserMemory:
    def __init__(self, storage_path: str | Path = "data/memory.json") -> None:
        self._storage_path = Path(storage_path)
        self._items: dict[str, MemoryItem] = {}
        self._logger = get_logger("memory")
        self._load()

    def _load(self) -> None:
        if not self._storage_path.exists():
            return
        try:
            with open(self._storage_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                for key, item_data in data.get("items", {}).items():
                    self._items[key] = MemoryItem.from_dict(item_data)
        except (json.JSONDecodeError, OSError, KeyError) as e:
            self._logger.warning(f"Failed to load memory: {e}")

    def save(self) -> None:
        self._storage_path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "version": 1,
            "updated_at": datetime.now().isoformat(),
            "items": {k: v.to_dict() for k, v in self._items.items()},
        }
        with open(self._storage_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    def store(self, key: str, value: str, category: str = "general", confidence: float = 1.0, source: str = "conversation") -> None:
        normalized_key = key.lower().strip()
        if normalized_key in self._items:
            existing = self._items[normalized_key]
            existing.value = value
            existing.category = category
            existing.confidence = confidence
            existing.source = source
            existing.touch()
        else:
            self._items[normalized_key] = MemoryItem(
                key=normalized_key, value=value, category=category, confidence=confidence, source=source
            )
        self.save()

    def retrieve(self, key: str) -> MemoryItem | None:
        normalized_key = key.lower().strip()
        item = self._items.get(normalized_key)
        if item is not None:
            item.touch()
        return item

    def search(self, query: str, limit: int = 5) -> list[MemoryItem]:
        query_lower = query.lower().strip()
        results: list[tuple[int, MemoryItem]] = []
        for item in self._items.values():
            score = 0
            if query_lower in item.key:
                score += 10
            if query_lower in item.value.lower():
                score += 5
            if any(w in item.key or w in item.value.lower() for w in query_lower.split()):
                score += 2
            if score > 0:
                results.append((score, item))
        results.sort(key=lambda x: (-x[0], -x[1].access_count))
        return [item for _, item in results[:limit]]

    def get_by_category(self, category: str) -> list[MemoryItem]:
        return [item for item in self._items.values() if item.category == category]

    def get_all(self) -> list[MemoryItem]:
        return list(self._items.values())

    def delete(self, key: str) -> bool:
        normalized_key = key.lower().strip()
        if normalized_key in self._items:
            del self._items[normalized_key]
            self.save()
            return True
        return False

    def clear(self) -> None:
        self._items.clear()
        self.save()

    def extract_from_text(self, text: str) -> list[tuple[str, str, str, str]]:
        extracted: list[tuple[str, str, str, str]] = []
        text_lower = text.lower()
        for pattern, category, source in _PREFERENCE_PATTERNS:
            matches = re.finditer(pattern, text_lower, re.IGNORECASE)
            for match in matches:
                value = match.group(1).strip()
                if len(value) < 2 or len(value) > 200:
                    continue
                key = f"{category}:{value[:50]}"
                extracted.append((key, value, category, source))
        return extracted

    def build_context_string(self, max_items: int = 10) -> str:
        items = sorted(self._items.values(), key=lambda x: (-x.confidence, -x.access_count))
        if not items:
            return ""
        selected = items[:max_items]
        parts: list[str] = ["## User Profile (remembered from previous conversations)"]
        categories: dict[str, list[str]] = {}
        for item in selected:
            categories.setdefault(item.category, []).append(f"- {item.key}: {item.value}")
        for cat, entries in categories.items():
            parts.append(f"### {cat.title()}")
            parts.extend(entries)
        return "\n".join(parts)

    def __len__(self) -> int:
        return len(self._items)


class EpisodicMemory:
    """Stores session summaries as episodic memories, persisted to disk."""

    def __init__(self, storage_path: str | Path = "data/episodic_memory.json", max_episodes: int = 50) -> None:
        self._storage_path = Path(storage_path)
        self._episodes: list[dict[str, Any]] = []
        self._max = max_episodes
        self._logger = get_logger("episodic_memory")
        self._load()

    def _load(self) -> None:
        if not self._storage_path.exists():
            return
        try:
            with open(self._storage_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                self._episodes = data.get("episodes", [])
        except (json.JSONDecodeError, OSError) as e:
            self._logger.warning(f"Failed to load episodic memory: {e}")

    def save(self) -> None:
        self._storage_path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "version": 1,
            "updated_at": datetime.now().isoformat(),
            "episodes": self._episodes[-self._max:],
        }
        with open(self._storage_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    def store_episode(self, summary: str, session_id: str = "", tool_calls: int = 0) -> None:
        self._episodes.append({
            "summary": summary,
            "session_id": session_id,
            "tool_calls": tool_calls,
            "timestamp": datetime.now().isoformat(),
        })
        if len(self._episodes) > self._max:
            self._episodes = self._episodes[-self._max:]
        self.save()

    def recall(self, query: str = "", limit: int = 5) -> list[dict[str, Any]]:
        if not query:
            return self._episodes[-limit:]
        query_lower = query.lower()
        scored = []
        for ep in self._episodes:
            if query_lower in ep.get("summary", "").lower():
                scored.append(ep)
        return scored[-limit:] if scored else self._episodes[-limit:]

    def build_context_string(self, limit: int = 3) -> str:
        recent = self._episodes[-limit:]
        if not recent:
            return ""
        parts = ["## Recent Session History"]
        for ep in recent:
            ts = ep.get("timestamp", "")[:10]
            parts.append(f"- [{ts}] {ep.get('summary', '')}")
        return "\n".join(parts)

    def __len__(self) -> int:
        return len(self._episodes)


class ProceduralMemory:
    """Loads persistent rules/playbooks from a directory of markdown files."""

    def __init__(self, procedures_dir: str | Path = "data/procedures") -> None:
        self._dir = Path(procedures_dir)
        self._procedures: dict[str, str] = {}
        self._load()

    def _load(self) -> None:
        if not self._dir.exists():
            return
        for md in sorted(self._dir.glob("*.md")):
            try:
                self._procedures[md.stem] = md.read_text(encoding="utf-8")
            except OSError:
                pass

    def get(self, name: str) -> str | None:
        return self._procedures.get(name)

    def list_procedures(self) -> list[str]:
        return sorted(self._procedures.keys())

    def build_context_string(self, max_chars: int = 1500) -> str:
        if not self._procedures:
            return ""
        parts = ["## Procedural Rules"]
        total = 0
        for name, content in self._procedures.items():
            if total + len(content) > max_chars:
                break
            parts.append(f"### {name}")
            parts.append(content.strip())
            total += len(content)
        return "\n".join(parts)

    def __len__(self) -> int:
        return len(self._procedures)
