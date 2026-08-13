"""Small domain exceptions shared across Core boundaries."""
from __future__ import annotations


class PCAssistantError(Exception):
    """Base exception for expected domain failures."""


class ToolNotFoundError(PCAssistantError, KeyError):
    def __init__(self, tool_name: str) -> None:
        self.tool_name = tool_name
        PCAssistantError.__init__(
            self,
            f"Tool '{tool_name}' not found in registry",
        )


class SessionNotFoundError(PCAssistantError):
    """Session is unknown or is not owned by the current principal."""

    def __init__(self) -> None:
        super().__init__("Session not found")
