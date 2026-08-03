from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class ToolBase(ABC):
    name: str = ""
    description: str = ""
    is_side_effecting: bool = False

    @abstractmethod
    async def execute(self, **kwargs: Any) -> Any:
        ...

    @abstractmethod
    def schema(self) -> dict[str, Any]:
        """Return full JSON schema for this tool."""

    def core_schema(self) -> dict[str, Any]:
        """Return concise core schema for cache-friendly static injection.
        Override to provide a minimal schema with only essential parameters.
        """
        return self.schema()

    def __repr__(self) -> str:
        return f"<Tool {self.name}>"
