from __future__ import annotations

from pc_assistant.exceptions import (
    PCAssistantError,
    SessionNotFoundError,
    ToolNotFoundError,
)


def test_tool_not_found_carries_bounded_identity() -> None:
    error = ToolNotFoundError("missing")

    assert isinstance(error, PCAssistantError)
    assert isinstance(error, KeyError)
    assert error.tool_name == "missing"


def test_session_not_found_is_generic() -> None:
    error = SessionNotFoundError()

    assert isinstance(error, PCAssistantError)
    assert str(error) == "Session not found"
