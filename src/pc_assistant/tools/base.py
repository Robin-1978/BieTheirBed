from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ToolParameter:
    internal_name: str
    public_name: str
    required: bool | None = None
    description: str = ""
    example: Any = None
    skim: bool = False
    skim_hint: str = ""


def parameter(
    internal_name: str,
    *,
    public_name: str | None = None,
    required: bool | None = None,
    description: str = "",
    example: Any = None,
    skim: bool = False,
    skim_hint: str = "",
):
    """Declare one model-facing parameter next to its tool."""
    def decorate(cls):
        current = tuple(getattr(cls, "llm_parameters", ()))
        cls.llm_parameters = current + (
            ToolParameter(
                internal_name=internal_name,
                public_name=public_name or internal_name,
                required=required,
                description=description,
                example=example,
                skim=skim,
                skim_hint=skim_hint,
            ),
        )
        return cls

    return decorate


def tool(
    *,
    name: str,
    description: str,
    skim_description: str = "",
    details: str = "",
    examples: list[dict[str, Any]] | None = None,
):
    """Declare the concise name and description exposed to the model."""
    if not name or not description:
        raise ValueError("tool name and description are required")

    def decorate(cls):
        cls.llm_name = name
        cls.llm_description = description
        cls.llm_skim_description = skim_description or description
        cls.llm_details = details
        cls.llm_examples = list(examples or [])
        return cls

    return decorate


class ToolBase(ABC):
    name: str = ""
    description: str = ""
    is_side_effecting: bool = False
    llm_name: str | None = None
    llm_description: str | None = None
    llm_skim_description: str | None = None
    llm_parameters: tuple[ToolParameter, ...] = ()
    llm_details: str = ""
    llm_examples: list[dict[str, Any]] = []

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
