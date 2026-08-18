[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [Parameter(Mandatory = $true)][string[]]$TunnelNames,
    [string]$InstallRoot = "$env:ProgramData\Cloudflared",
    [switch]$PurgeData
)

$ErrorActionPreference = "Stop"
foreach ($nameValue in $TunnelNames) {
    $name = $nameValue.Trim()
    if ($name -notmatch '^[A-Za-z][A-Za-z0-9_-]{0,31}$') {
        throw "Tunnel name '$name' is invalid"
    }
    $serviceId = "Cloudflared-$name"
    $wrapper = Join-Path $InstallRoot "Services\$serviceId\$serviceId.exe"
    if ((Test-Path -LiteralPath $wrapper) -and
        (Get-Service -Name $serviceId -ErrorAction SilentlyContinue)) {
        & $wrapper stop | Out-Null
        & $wrapper uninstall | Out-Null
    }
}
if ($PurgeData -and $PSCmdlet.ShouldProcess($InstallRoot, "Delete Cloudflare Tunnel runtime, logs and Token files")) {
    Remove-Item -LiteralPath $InstallRoot -Recurse -Force -ErrorAction SilentlyContinue
}
Write-Host "Cloudflare Tunnel services removed. Token files were preserved unless -PurgeData was supplied."

