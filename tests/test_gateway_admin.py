from __future__ import annotations

import base64

import pytest

from pc_assistant import main
from pc_assistant.gateway.identity import (
    DeviceNotFoundError,
    GatewayIdentityRepository,
)


def test_gateway_admin_pairs_lists_and_revokes_device(
    tmp_path,
    capsys,
    monkeypatch,
) -> None:
    monkeypatch.delenv("PC_RUNTIME_ROOT", raising=False)
    monkeypatch.delenv("PC_ASSISTANT_HOME", raising=False)
    runtime_root = tmp_path / "runtime"
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        f"runtime_root: {runtime_root}\nowner_principal_id: personal:robin\n",
        encoding="utf-8",
    )

    assert main(["--config", str(config_path), "gateway", "pair", "--ttl", "60"]) == 0
    created = dict(
        line.split("=", 1)
        for line in capsys.readouterr().out.splitlines()
        if "=" in line
    )
    repository = GatewayIdentityRepository(runtime_root / "data" / "gateway.db")
    device = repository.register_verified_device(
        created["grant_id"],
        created["grant_secret"],
        display_name="Robin Phone",
        public_key=base64.urlsafe_b64encode(b"k" * 32).decode().rstrip("="),
    )

    assert main(["--config", str(config_path), "gateway", "devices"]) == 0
    listed = capsys.readouterr().out
    assert device.device_id in listed
    assert "Robin Phone" in listed
    assert "active" in listed

    assert main(
        ["--config", str(config_path), "gateway", "revoke", device.device_id]
    ) == 0
    assert capsys.readouterr().out.strip() == f"revoked={device.device_id}"
    with pytest.raises(DeviceNotFoundError):
        repository.active_device("personal:robin", device.device_id)


def test_gateway_admin_rejects_invalid_pairing_ttl() -> None:
    with pytest.raises(SystemExit) as exc_info:
        main(["gateway", "pair", "--ttl", "10"])
    assert exc_info.value.code == 2
