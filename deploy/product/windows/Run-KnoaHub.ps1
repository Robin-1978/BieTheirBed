[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Updater,
    [Parameter(Mandatory = $true)][string]$ReleaseRoot,
    [Parameter(Mandatory = $true)][string]$HubRoot,
    [Parameter(Mandatory = $true)][string]$BootstrapTokenFile,
    [Parameter(Mandatory = $true)][string]$ReleasePublishTokenFile,
    [Parameter(Mandatory = $true)][string]$LifecycleTokenFile,
    [Parameter(Mandatory = $true)][string]$LifecycleIncomingRoot,
    [Parameter(Mandatory = $true)][string]$PublicUrl,
    [int]$Port = 9529,
    [int]$ConsolePort = 9532
)

$ErrorActionPreference = "Stop"
$env:KNOA_HUB_BOOTSTRAP_TOKEN = (Get-Content -LiteralPath $BootstrapTokenFile -Raw).Trim()
$env:KNOA_HUB_RELEASE_PUBLISH_TOKEN = (Get-Content -LiteralPath $ReleasePublishTokenFile -Raw).Trim()
$env:KNOA_LIFECYCLE_TOKEN_FILE = $LifecycleTokenFile
$env:KNOA_LIFECYCLE_INCOMING_ROOT = $LifecycleIncomingRoot
& $Updater run `
    --install-root $ReleaseRoot `
    --entrypoint bin/knoa-hub.cmd `
    -- `
    --deployment-mode hosted_single_node `
    --host 127.0.0.1 `
    --port $Port `
    --public-url $PublicUrl `
    --console-host 127.0.0.1 `
    --console-port $ConsolePort `
    --root $HubRoot `
    --hub-id hub_knoa_hosted
exit $LASTEXITCODE
