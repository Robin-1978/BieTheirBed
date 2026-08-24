from __future__ import annotations

import json

from knoa_platform.channels.settings import ChannelSettingsStore
from knoa_platform.config import AppConfig


def test_dingtalk_settings_are_private_and_never_echo_the_secret(tmp_path) -> None:
    base = AppConfig(runtime_root=str(tmp_path), fallback_enabled=False)
    store = ChannelSettingsStore(tmp_path, clock=lambda: 10)

    effective = store.configure_dingtalk(
        base,
        enabled=True,
        client_id="ding-client",
        client_secret="super-secret-value",
        robot_code="ding-robot",
        receive_id="",
    )
    status = store.status(base, running=True)

    assert effective.dingtalk_enabled is True
    assert effective.dingtalk_client_secret.get_secret_value() == "super-secret-value"
    assert status["client_secret_configured"] is True
    assert status["running"] is True
    assert "super-secret-value" not in json.dumps(status)
    assert (
        "super-secret-value"
        not in (tmp_path / "data" / "channel-settings.json").read_text()
    )
    assert (tmp_path / "data" / "channel-settings.json").stat().st_mode & 0o077 == 0
    assert (
        tmp_path / "secrets" / "channels" / "dingtalk.client_secret"
    ).stat().st_mode & 0o077 == 0


def test_dingtalk_settings_preserve_existing_secret_on_non_secret_edit(
    tmp_path,
) -> None:
    base = AppConfig(runtime_root=str(tmp_path), fallback_enabled=False)
    store = ChannelSettingsStore(tmp_path)
    store.configure_dingtalk(
        base,
        enabled=True,
        client_id="ding-client",
        client_secret="first-secret",
        robot_code="",
        receive_id="",
    )

    effective = store.configure_dingtalk(
        base,
        enabled=True,
        client_id="ding-client-updated",
        client_secret="",
        robot_code="ding-robot",
        receive_id="staff-owner",
    )

    assert effective.dingtalk_client_id == "ding-client-updated"
    assert effective.dingtalk_client_secret.get_secret_value() == "first-secret"
    assert effective.dingtalk_receive_id == "staff-owner"
