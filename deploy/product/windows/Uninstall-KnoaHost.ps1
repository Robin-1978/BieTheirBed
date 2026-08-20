[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
& reg.exe delete "HKLM\Software\Microsoft\Windows\CurrentVersion\Run" /v "KnoaDesktopCompanion" /f 2>$null | Out-Null
Get-NetFirewallRule -Name "KnoaNodeWebRtcP2P" -ErrorAction SilentlyContinue | `
    Remove-NetFirewallRule -ErrorAction SilentlyContinue
foreach ($name in @("KnoaHostedHub", "KnoaNode", "KnoaHostLifecycle")) {
    $service = Get-Service -Name $name -ErrorAction SilentlyContinue
    if ($service) {
        if ($service.Status -ne "Stopped") {
            Stop-Service -Name $name -Force -ErrorAction SilentlyContinue
        }
        $wrapper = Join-Path $env:ProgramData "Knoa\Services\$name\$name.exe"
        if (Test-Path -LiteralPath $wrapper) {
            & $wrapper uninstall | Out-Null
        } else {
            & sc.exe delete $name | Out-Null
        }
    }
}
Write-Host "Knoa services were removed. Data under $env:ProgramData\Knoa was retained."
