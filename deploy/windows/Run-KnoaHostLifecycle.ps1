[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$PythonExecutable,
    [Parameter(Mandatory = $true)][string]$SourceRoot,
    [Parameter(Mandatory = $true)][string]$SourceStateFile,
    [Parameter(Mandatory = $true)][string]$SourceSnapshotsRoot,
    [Parameter(Mandatory = $true)][string]$InstallationStateFile,
    [Parameter(Mandatory = $true)][string]$TokenFile
)

$ErrorActionPreference = "Stop"
& $PythonExecutable -m knoa_platform.host_lifecycle `
    --mode source `
    --host 127.0.0.1 `
    --port 9533 `
    --source-root $SourceRoot `
    --source-state-file $SourceStateFile `
    --source-snapshots-root $SourceSnapshotsRoot `
    --installation-state-file $InstallationStateFile `
    --token-file $TokenFile
exit $LASTEXITCODE
