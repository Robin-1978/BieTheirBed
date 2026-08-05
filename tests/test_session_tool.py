from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from pc_assistant.tools.session import SessionTool


def _completed(stdout: str = "", stderr: str = "", returncode: int = 0):
    return MagicMock(stdout=stdout, stderr=stderr, returncode=returncode)


@pytest.mark.asyncio
async def test_session_status_reports_lock_state(monkeypatch):
    monkeypatch.setenv("XDG_SESSION_ID", "7")
    output = "Type=x11\nActive=yes\nState=active\nLockedHint=no\nRemote=no\n"
    with patch("pc_assistant.tools.session.subprocess.run", return_value=_completed(output)):
        result = await SessionTool().execute(action="status")

    assert result == {
        "session_id": "7",
        "type": "x11",
        "active": True,
        "state": "active",
        "locked": False,
        "remote": False,
    }


@pytest.mark.asyncio
async def test_session_lock_must_be_verified(monkeypatch):
    monkeypatch.setenv("XDG_SESSION_ID", "7")
    unlocked = "Type=x11\nActive=yes\nState=active\nLockedHint=no\nRemote=no\n"
    with patch(
        "pc_assistant.tools.session.subprocess.run",
        side_effect=[_completed(), _completed(unlocked)],
    ):
        result = await SessionTool().execute(action="lock")

    assert result["error"] == "Lock request was not verified"


@pytest.mark.asyncio
async def test_session_lock_reports_success_only_after_locked_hint(monkeypatch):
    monkeypatch.setenv("XDG_SESSION_ID", "7")
    locked = "Type=x11\nActive=yes\nState=active\nLockedHint=yes\nRemote=no\n"
    with patch(
        "pc_assistant.tools.session.subprocess.run",
        side_effect=[_completed(), _completed(locked)],
    ):
        result = await SessionTool().execute(action="lock")

    assert result["success"] is True
    assert result["locked"] is True


@pytest.mark.asyncio
async def test_session_tool_has_no_unlock_action(monkeypatch):
    monkeypatch.setenv("XDG_SESSION_ID", "7")
    result = await SessionTool().execute(action="unlock")
    assert "Unknown session action" in result["error"]
