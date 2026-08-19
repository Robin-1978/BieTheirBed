from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_windows_installer_uses_python314_and_winsw_hub() -> None:
    script = _read("deploy/windows/Install-Knoa.ps1")

    assert '[string]$PythonVersion = "3.14"' in script
    assert "Py_GIL_DISABLED" in script
    assert "[string]$WinSWExecutable" in script
    assert 'Install-WinSWService "KnoaHostedHub"' in script
    assert 'Register-ScheduledTask -TaskName "Knoa Hosted Hub"' not in script


def test_windows_node_is_always_a_winsw_service() -> None:
    script = _read("deploy/windows/Install-Knoa.ps1")

    assert 'Install-WinSWService "KnoaNode"' in script
    assert 'Register-ScheduledTask -TaskName "Knoa Node"' not in script
    assert "$env:ProgramData\\Knoa\\Node" in script
    assert "gateway pair --ttl 600" in script
    assert "\n    -and " not in script


def test_windows_enrollment_restarts_service_and_prints_pairing_qr() -> None:
    script = _read("deploy/windows/Enroll-KnoaNode.ps1")

    assert "Restart-Service KnoaNode" in script
    assert "gateway pair --ttl $PairingTtlSeconds" in script

    pairing = _read("deploy/windows/Show-KnoaPairingQr.cmd")
    assert "node-windows.yaml" in pairing
    assert "gateway pair --ttl 300" in pairing


def test_windows_cloudflared_supports_independent_token_files() -> None:
    script = _read("deploy/windows/Install-Cloudflared.ps1")

    assert "[string[]]$TunnelNames" in script
    assert "[string[]]$TunnelTokenFiles" in script
    assert "--token-file" in script
    assert "service install" not in script
    assert 'serviceId = "Cloudflared-$name"' in script


def test_linux_cloudflared_services_also_keep_tokens_out_of_arguments() -> None:
    knoa = _read("deploy/cloudflared/cloudflared-knoa.user.service")
    per = _read("deploy/cloudflared/cloudflared-per.user.service")

    assert "--token-file" in knoa
    assert "--token-file" in per
    assert "--token ${" not in knoa
    assert "--token ${" not in per
