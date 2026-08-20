[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$PythonExecutable,
    [Parameter(Mandatory = $true)][string]$NodeRoot,
    [Parameter(Mandatory = $true)][string]$ConfigPath,
    [Parameter(Mandatory = $true)][string]$DesktopCompanionTokenFile,
    [string]$LifecycleTokenFile = "",
    [string]$LifecycleIncomingRoot = ""
)

$ErrorActionPreference = "Stop"
$env:KNOA_RUNTIME_ROOT = $NodeRoot
$env:KNOA_RUNTIME_DIR = Join-Path $NodeRoot "run"
$env:KNOA_DESKTOP_COMPANION_TOKEN_FILE = $DesktopCompanionTokenFile
if ($LifecycleTokenFile -and $LifecycleIncomingRoot) {
    $env:KNOA_LIFECYCLE_TOKEN_FILE = $LifecycleTokenFile
    $env:KNOA_LIFECYCLE_INCOMING_ROOT = $LifecycleIncomingRoot
}
& $PythonExecutable -m knoa_platform.service --config $ConfigPath
exit $LASTEXITCODE
