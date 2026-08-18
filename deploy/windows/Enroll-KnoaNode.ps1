[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$WorkspaceId,
    [Parameter(Mandatory = $true)][string]$AccountTokenFile,
    [string]$HubPublicUrl = "https://knoa.tinydotdot.com",
    [string]$NodeRoot = "$env:ProgramData\Knoa\Node",
    [string]$PythonExecutable = "$env:ProgramData\Knoa\Runtime\venv\Scripts\python.exe",
    [string]$ConfigPath = "$env:ProgramData\Knoa\Config\node-windows.yaml",
    [string]$DisplayName = $env:COMPUTERNAME,
    [int]$PairingTtlSeconds = 600
)

$ErrorActionPreference = "Stop"
$token = (Get-Content -LiteralPath $AccountTokenFile -Raw).Trim()
if ($token.Length -lt 32) { throw "Hub Account token is invalid" }
$env:KNOA_HUB_ACCOUNT_TOKEN = $token
try {
    & $PythonExecutable -m knoa_platform.hub.admin node-enroll `
        --hub-url $HubPublicUrl `
        --workspace-id $WorkspaceId `
        --runtime-root $NodeRoot `
        --display-name $DisplayName
    if ($LASTEXITCODE -ne 0) { throw "Windows Node enrollment failed" }
} finally {
    Remove-Item Env:KNOA_HUB_ACCOUNT_TOKEN -ErrorAction SilentlyContinue
    $token = $null
}
Restart-Service KnoaNode
Write-Host "Scan this QR in the Knoa App to bind the Windows Node:"
& $PythonExecutable -m knoa_platform --config $ConfigPath gateway pair --ttl $PairingTtlSeconds
if ($LASTEXITCODE -ne 0) { throw "Could not create the App pairing QR" }
