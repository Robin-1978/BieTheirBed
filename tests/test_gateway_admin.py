from __future__ import annotations

import base64
import json
import zipfile

import pytest

from knoa_platform import main
from knoa_platform.gateway.identity import (
    DeviceNotFoundError,
    GatewayIdentityRepository,
)


def test_gateway_admin_pairs_lists_and_revokes_device(
    tmp_path,
    capsys,
    monkeypatch,
) -> None:
    monkeypatch.delenv("KNOA_RUNTIME_ROOT", raising=False)
    monkeypatch.delenv("KNOA_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "user-home"))
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


def test_gateway_admin_emits_canonical_pairing_payload_when_url_is_configured(
    tmp_path,
    capsys,
    monkeypatch,
) -> None:
    monkeypatch.delenv("KNOA_RUNTIME_ROOT", raising=False)
    runtime_root = tmp_path / "runtime"
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "\n".join(
            (
                f"runtime_root: {runtime_root}",
                "gateway_public_url: https://knoa.example.com",
            )
        ),
        encoding="utf-8",
    )

    assert main(["--config", str(config_path), "gateway", "pair", "--ttl", "60"]) == 0

    output = capsys.readouterr().out
    encoded = next(
        line.removeprefix("pairing_json=")
        for line in output.splitlines()
        if line.startswith("pairing_json=")
    )
    payload = json.loads(encoded)
    assert payload["version"] == "v2"
    assert payload["gateway_url"] == "https://knoa.example.com"
    assert payload["grant_id"]
    assert len(payload["grant_secret"]) >= 32
    assert payload["node_id"].startswith("node_")
    assert len(payload["node_signing_public_key"]) >= 40
    assert len(payload["node_configuration_public_key"]) >= 40


def test_gateway_admin_publishes_and_inspects_private_android_release(
    tmp_path,
    capsys,
    monkeypatch,
) -> None:
    monkeypatch.delenv("KNOA_RUNTIME_ROOT", raising=False)
    runtime_root = tmp_path / "runtime"
    config_path = tmp_path / "config.yaml"
    config_path.write_text(f"runtime_root: {runtime_root}\n", encoding="utf-8")
    monkeypatch.setattr(
        "knoa_platform.gateway.release_admin.read_apk_version",
        lambda _apk: ("0.2.0", 2),
    )
    apk = tmp_path / "knoa.apk"
    with zipfile.ZipFile(apk, "w") as archive:
        archive.writestr("AndroidManifest.xml", b"manifest")
        archive.writestr("classes.dex", b"private android package")

    assert (
        main(
            [
                "--config",
                str(config_path),
                "gateway",
                "release",
                "publish",
                str(apk),
                "--notes",
                "私人更新",
            ]
        )
        == 0
    )
    published = capsys.readouterr().out
    assert "version_code=2" in published
    assert "sha256=" in published

    assert main(["--config", str(config_path), "gateway", "release", "latest"]) == 0
    latest = capsys.readouterr().out
    assert "version_name=0.2.0" in latest
    assert "version_code=2" in latest
