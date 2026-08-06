from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ToolParameter:
    """Metadata for a single tool parameter visible in skim schema."""

    name: str
    description: str = ""
    required: bool | None = None
    skim: bool = True


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
    is_side_effecting: bool = False
    _declared_parameters: tuple[ToolParameter, ...] = ()

    @abstractmethod
    async def execute(self, **kwargs: Any) -> Any: ...

    @abstractmethod
    def schema(self) -> dict[str, Any]:
        """Full JSON schema (returned by tool_help)."""

    def skim_schema(self) -> dict[str, Any]:
        """Compact schema for LLM injection. Override to omit rare params."""
        return self.schema()

    def __repr__(self) -> str:
        return f"<Tool {self.name}>"
