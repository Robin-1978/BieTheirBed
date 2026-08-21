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
# 9530 bound by an orphaned release.
function Quote-ProcessArgument([string]$Value) {
    return '"' + $Value.Replace('"', '\"') + '"'
}

$psi = New-Object System.Diagnostics.ProcessStartInfo
$psi.FileName = $Updater
$psi.Arguments = @(
    "run",
    "--install-root", (Quote-ProcessArgument $ReleaseRoot),
    "--entrypoint", "bin/knoa-node.cmd",
    "--",
    "--config", (Quote-ProcessArgument $ConfigPath)
) -join " "
$psi.UseShellExecute = $false
$child = [System.Diagnostics.Process]::Start($psi)

function Stop-Child {
    if ($child -and -not $child.HasExited) {
        & taskkill.exe /F /T /PID $child.Id 2>$null
    }
}

[Console]::CancelKeyPress.AddHandler({ Stop-Child })
trap { Stop-Child; break }

try {
    $child.WaitForExit()
    exit $child.ExitCode
} finally {
    Stop-Child
}
