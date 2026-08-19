[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Updater,
    [Parameter(Mandatory = $true)][string]$ReleaseRoot,
    [Parameter(Mandatory = $true)][string]$TrustStore,
    [Parameter(Mandatory = $true)][string]$StateFile,
    [Parameter(Mandatory = $true)][string]$IncomingRoot,
    [Parameter(Mandatory = $true)][string]$TokenFile
)

$ErrorActionPreference = "Stop"
& $Updater run `
    --install-root $ReleaseRoot `
    --entrypoint bin/knoa-host-lifecycle.cmd `
    -- `
    --host 127.0.0.1 `
    --port 9533 `
    --updater $Updater `
    --release-root $ReleaseRoot `
    --trust-store $TrustStore `
    --state-file $StateFile `
    --incoming-root $IncomingRoot `
    --token-file $TokenFile
exit $LASTEXITCODE
