from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_windows_installer_uses_python314_and_winsw_hub() -> None:
    script = _read("deploy/windows/Install-Knoa.ps1")

    assert '[string]$PythonVersion = "3.14"' in script
    assert "Py_GIL_DISABLED" in script
    assert '[string]$WinSWExecutable' in script
    assert 'Install-WinSWService "KnoaHostedHub"' in script
    assert 'Register-ScheduledTask -TaskName "Knoa Hosted Hub"' not in script


def test_windows_node_has_explicit_interactive_and_headless_modes() -> None:
    script = _read("deploy/windows/Install-Knoa.ps1")

    assert 'ValidateSet("InteractiveTask", "HeadlessService")' in script
    assert 'Register-ScheduledTask -TaskName "Knoa Node"' in script
    assert 'Install-WinSWService "KnoaNode"' in script
    assert "Session 0" in script


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
