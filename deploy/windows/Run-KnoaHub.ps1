[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$PythonExecutable,
    [Parameter(Mandatory = $true)][string]$HubRoot,
    [Parameter(Mandatory = $true)][string]$BootstrapTokenFile,
    [Parameter(Mandatory = $true)][string]$ReleasePublishTokenFile,
    [string]$LifecycleTokenFile = "",
    [string]$LifecycleIncomingRoot = "",
    [string]$HubId = "hub_knoa_hosted",
    [int]$Port = 9529,
    [int]$ConsolePort = 9532,
    [string]$PublicUrl = "http://127.0.0.1:9529"
)

$ErrorActionPreference = "Stop"
$token = (Get-Content -LiteralPath $BootstrapTokenFile -Raw).Trim()
if ($token.Length -lt 32) {
    throw "Knoa Hub bootstrap token must contain at least 32 characters"
}
$env:KNOA_HUB_BOOTSTRAP_TOKEN = $token
$releasePublishToken = (Get-Content -LiteralPath $ReleasePublishTokenFile -Raw).Trim()
if ($releasePublishToken.Length -lt 32) {
    throw "Knoa Hub release publisher token must contain at least 32 characters"
}
$env:KNOA_HUB_RELEASE_PUBLISH_TOKEN = $releasePublishToken
if ($LifecycleTokenFile -and $LifecycleIncomingRoot) {
    $env:KNOA_LIFECYCLE_TOKEN_FILE = $LifecycleTokenFile
    $env:KNOA_LIFECYCLE_INCOMING_ROOT = $LifecycleIncomingRoot
}
& $PythonExecutable -m knoa_platform.hub `
    --deployment-mode hosted_single_node `
    --host 127.0.0.1 `
    --port $Port `
    --public-url $PublicUrl `
    --console-host 127.0.0.1 `
    --console-port $ConsolePort `
    --root $HubRoot `
    --hub-id $HubId
exit $LASTEXITCODE
