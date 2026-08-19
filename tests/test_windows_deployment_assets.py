from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_windows_installer_uses_python314_and_winsw_hub() -> None:
    script = _read("deploy/windows/Install-Knoa.ps1")

    assert '[ValidateSet("all", "hub", "node")]' in script
    assert '[string]$Role = "all"' in script
    assert '$installHub = $Role -in @("all", "hub")' in script
    assert '$installNode = $Role -in @("all", "node")' in script
    assert '[string]$PythonVersion = "3.14"' in script
    assert "Py_GIL_DISABLED" in script
    assert "$pythonProbe | & $python -" in script
    assert "& $python -c" not in script
    assert "[string]$WinSWExecutable" in script
    assert 'Install-WinSWService "KnoaHostedHub"' in script
    assert "if ($installHub)" in script
    assert 'Register-ScheduledTask -TaskName "Knoa Hosted Hub"' not in script
    assert 'Join-Path $configRoot "installation.json"' in script
    assert 'Copy-Item -Force (Join-Path $PSScriptRoot "Update-Knoa.cmd")' in script
    assert "-and -not $SkipPairingQr" in script


def test_windows_node_is_always_a_winsw_service() -> None:
    script = _read("deploy/windows/Install-Knoa.ps1")

    assert 'Install-WinSWService "KnoaNode"' in script
    assert "if ($installNode)" in script
    assert 'Register-ScheduledTask -TaskName "Knoa Node"' not in script
    assert "$env:ProgramData\\Knoa\\Node" in script
    assert "gateway pair --ttl 600" in script
    assert "\n    -and " not in script


def test_windows_installer_stops_services_before_updating_runtime() -> None:
    script = _read("deploy/windows/Install-Knoa.ps1")

    stop_hub = 'if ($installHub) { Stop-KnoaService "KnoaHostedHub" }'
    stop_node = 'if ($installNode) { Stop-KnoaService "KnoaNode" }'
    install_package = "& $python -m pip install"
    assert stop_hub in script
    assert stop_node in script
    assert script.index(stop_hub) < script.index(install_package)
    assert script.index(stop_node) < script.index(install_package)


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


def test_windows_hosted_hub_can_publish_the_android_app() -> None:
    script = _read("deploy/windows/Publish-KnoaApp.ps1")

    assert '"knoa_platform.hub.admin", "mobile-publish"' in script
    assert "AppMetadataPath" in script
    assert "ReleaseMetadataPath" in script
    assert 'ChangeExtension($resolvedApk, ".release.json")' in script
    assert "Android release metadata does not match the APK" in script
    assert "-Raw -Encoding UTF8" in script
    assert '"--version-code", $VersionCode' in script
    assert "knoa_platform.hub.admin mobile-latest" in script
    assert "/downloads/android/latest.apk" in script


def test_windows_one_click_updater_pulls_reinstalls_and_recovers_services() -> None:
    updater = _read("deploy/windows/Update-Knoa.ps1")
    launcher = _read("deploy/windows/Update-Knoa.cmd")

    assert "Start-Process" in updater
    assert "-Verb RunAs" in updater
    assert "pull --ff-only" in updater
    assert 'Join-Path $resolvedSource "deploy\\windows\\Install-Knoa.ps1"' in updater
    assert "Restart-InstalledServices $Role" in updater
    assert "SkipPairingQr = $true" in updater
    assert "local tracked changes" in updater
    assert "Update-Knoa.ps1" in launcher


def test_linux_cloudflared_services_also_keep_tokens_out_of_arguments() -> None:
    knoa = _read("deploy/cloudflared/cloudflared-knoa.user.service")
    per = _read("deploy/cloudflared/cloudflared-per.user.service")

    assert "--token-file" in knoa
    assert "--token-file" in per
    assert "--token ${" not in knoa
    assert "--token ${" not in per
