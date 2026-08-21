[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Updater,
    [Parameter(Mandatory = $true)][string]$ReleaseRoot,
    [Parameter(Mandatory = $true)][string]$NodeRoot,
    [Parameter(Mandatory = $true)][string]$ConfigPath,
    [Parameter(Mandatory = $true)][string]$LifecycleTokenFile,
    [Parameter(Mandatory = $true)][string]$LifecycleIncomingRoot,
    [Parameter(Mandatory = $true)][string]$DesktopCompanionTokenFile
)

$ErrorActionPreference = "Stop"
$env:KNOA_RUNTIME_ROOT = $NodeRoot
$env:KNOA_RUNTIME_DIR = Join-Path $NodeRoot "run"
$env:KNOA_LIFECYCLE_TOKEN_FILE = $LifecycleTokenFile
$env:KNOA_LIFECYCLE_INCOMING_ROOT = $LifecycleIncomingRoot
$env:KNOA_DESKTOP_COMPANION_TOKEN_FILE = $DesktopCompanionTokenFile

# Keep the updater (and the runtime it launches) under this runner's process
# tree so WinSW can stop the complete Node process tree without leaving port
# 9530 bound by an orphaned release. Start-Process -PassThru is reliable in
# WinSW's session-0 service context, unlike Process.Start for console apps.
function Quote-ProcessArgument([string]$Value) {
    return '"' + $Value.Replace('"', '\"') + '"'
}

$workingDirectory = Join-Path (Split-Path -Parent $NodeRoot) "Workspace"
if (-not (Test-Path -LiteralPath $workingDirectory)) {
    $workingDirectory = Split-Path -Parent $ConfigPath
}
$proc = Start-Process -FilePath $Updater `
    -ArgumentList @(
        "run",
        "--install-root", (Quote-ProcessArgument $ReleaseRoot),
        "--entrypoint", "bin/knoa-node.cmd",
        "--",
        "--config", (Quote-ProcessArgument $ConfigPath)
    ) `
    -PassThru -WindowStyle Hidden -WorkingDirectory $workingDirectory
if (-not $proc) { throw "Failed to launch Knoa updater" }

function Stop-Child {
    if ($proc -and -not $proc.HasExited) {
        & taskkill.exe /F /T /PID $proc.Id 2>$null
    }
}

[Console]::CancelKeyPress.AddHandler({ Stop-Child })
trap { Stop-Child; break }

try {
    $proc.WaitForExit()
    exit $proc.ExitCode
} finally {
    Stop-Child
}
