[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$PythonExecutable,
    [Parameter(Mandatory = $true)][string]$HubRoot,
    [Parameter(Mandatory = $true)][string]$BootstrapTokenFile,
    [string]$HubId = "hub_knoa_hosted",
    [int]$Port = 9529
)

$ErrorActionPreference = "Stop"
$token = (Get-Content -LiteralPath $BootstrapTokenFile -Raw).Trim()
if ($token.Length -lt 32) {
    throw "Knoa Hub bootstrap token must contain at least 32 characters"
}
$env:KNOA_HUB_BOOTSTRAP_TOKEN = $token
& $PythonExecutable -m knoa_platform.hub `
    --deployment-mode hosted_single_node `
    --host 127.0.0.1 `
    --port $Port `
    --root $HubRoot `
    --hub-id $HubId
exit $LASTEXITCODE
