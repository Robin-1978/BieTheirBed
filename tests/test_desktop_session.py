from __future__ import annotations

import os

import pytest

from pc_assistant import desktop_session


_DESKTOP_ENV_KEYS = (
    "DISPLAY",
    "XAUTHORITY",
    "WAYLAND_DISPLAY",
    "XDG_SESSION_TYPE",
    "XDG_RUNTIME_DIR",
    "DBUS_SESSION_BUS_ADDRESS",
)


def _clear_desktop_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in _DESKTOP_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)


def test_non_desktop_tools_bypass_session_recovery(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_desktop_environment(monkeypatch)
    monkeypatch.setattr(
        desktop_session,
        "_recover_desktop_environment",
        lambda: pytest.fail("non-desktop tool attempted session recovery"),
    )

    desktop_session.ensure_desktop_session("shell")


def test_existing_x11_environment_is_preserved(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_desktop_environment(monkeypatch)
    monkeypatch.setenv("DISPLAY", ":7")
    monkeypatch.setenv("XAUTHORITY", "/already/configured")
    monkeypatch.setattr(
        desktop_session,
        "_recover_desktop_environment",
        lambda: pytest.fail("usable environment should not be replaced"),
    )

    desktop_session.ensure_desktop_session("screenshot")

    assert os.environ["DISPLAY"] == ":7"
    assert os.environ["XAUTHORITY"] == "/already/configured"


def test_recovers_allowlisted_environment_from_active_session(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_desktop_environment(monkeypatch)
    session = desktop_session._GraphicalSession("3", 4321, "x11")
    monkeypatch.setattr(desktop_session, "_list_active_graphical_sessions", lambda: [session])
    monkeypatch.setattr(
        desktop_session,
        "_read_process_environment",
        lambda leader: {
            "DISPLAY": ":1",
            "XDG_RUNTIME_DIR": "/run/user/1000",
            "DBUS_SESSION_BUS_ADDRESS": "unix:path=/run/user/1000/bus",
            "LD_PRELOAD": "/untrusted/library.so",
        },
    )
    monkeypatch.setattr(
        desktop_session,
        "_select_xauthority",
        lambda environment: "/run/user/1000/gdm/Xauthority",
    )

    desktop_session.ensure_desktop_session("mouse")

    assert os.environ["DISPLAY"] == ":1"
    assert os.environ["XAUTHORITY"] == "/run/user/1000/gdm/Xauthority"
    assert os.environ["XDG_SESSION_TYPE"] == "x11"
    assert os.environ["DBUS_SESSION_BUS_ADDRESS"] == "unix:path=/run/user/1000/bus"
    assert os.environ.get("LD_PRELOAD") != "/untrusted/library.so"


def test_x11_recovery_can_infer_one_unambiguous_display(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_desktop_environment(monkeypatch)
    session = desktop_session._GraphicalSession("3", 4321, "x11")
    monkeypatch.setattr(desktop_session, "_list_active_graphical_sessions", lambda: [session])

    def unreadable_leader(leader):
        raise desktop_session.DesktopSessionError("session leader is owned by the display manager")

    monkeypatch.setattr(desktop_session, "_read_process_environment", unreadable_leader)
    monkeypatch.setattr(desktop_session, "_unique_x11_display", lambda: ":1")
    monkeypatch.setattr(
        desktop_session,
        "_select_xauthority",
        lambda environment: "/run/user/1000/gdm/Xauthority",
    )

    desktop_session.ensure_desktop_session("windows")

    assert os.environ["DISPLAY"] == ":1"


def test_recovery_fails_closed_without_changing_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_desktop_environment(monkeypatch)
    monkeypatch.setenv("XAUTHORITY", "/stale/authority")
    sessions = [
        desktop_session._GraphicalSession("3", 4321, "x11"),
        desktop_session._GraphicalSession("4", 5432, "wayland"),
    ]
    monkeypatch.setattr(desktop_session, "_list_active_graphical_sessions", lambda: sessions)

    with pytest.raises(desktop_session.DesktopSessionError, match="multiple"):
        desktop_session.ensure_desktop_session("ui")

    assert "DISPLAY" not in os.environ
    assert os.environ["XAUTHORITY"] == "/stale/authority"


def test_list_sessions_keeps_only_current_active_local_graphical_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    responses = {
        ("list-sessions", "--no-legend", "--no-pager"): "3 1000 robin seat0 tty2\n4 1001 other seat0 tty3\n5 1000 robin - pts/0\n",
        (
            "show-session",
            "3",
            "-p",
            "Active",
            "-p",
            "Remote",
            "-p",
            "Type",
            "-p",
            "Leader",
            "--no-pager",
        ): "Active=yes\nRemote=no\nType=x11\nLeader=4321\n",
        (
            "show-session",
            "5",
            "-p",
            "Active",
            "-p",
            "Remote",
            "-p",
            "Type",
            "-p",
            "Leader",
            "--no-pager",
        ): "Active=yes\nRemote=yes\nType=tty\nLeader=5555\n",
    }
    monkeypatch.setattr(os, "getuid", lambda: 1000)
    monkeypatch.setattr(desktop_session, "_run_loginctl", lambda *args: responses[args])

    assert desktop_session._list_active_graphical_sessions() == [
        desktop_session._GraphicalSession("3", 4321, "x11")
    ]
