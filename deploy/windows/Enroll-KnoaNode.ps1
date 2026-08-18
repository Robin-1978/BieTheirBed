[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$WorkspaceId,
    [Parameter(Mandatory = $true)][string]$AccountTokenFile,
    [string]$HubPublicUrl = "https://knoa.tinydotdot.com",
    [string]$NodeRoot = "$env:LOCALAPPDATA\Knoa\Node",
    [string]$PythonExecutable = "$env:ProgramData\Knoa\Runtime\venv\Scripts\python.exe",
    [string]$DisplayName = $env:COMPUTERNAME
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
Stop-ScheduledTask -TaskName "Knoa Node" -ErrorAction SilentlyContinue
Start-ScheduledTask -TaskName "Knoa Node"
