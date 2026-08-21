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
# 9530 bound by an orphaned release. The call operator is reliable in WinSW's
# session-0 context; CIM identifies the updater child for recursive cleanup.
$runnerPid = $PID

function Stop-Child {
    try {
        $children = @(Get-CimInstance Win32_Process `
            -Filter "ParentProcessId = $runnerPid" `
            -ErrorAction Stop | Where-Object {
                $_.Name -ieq "knoa-update.exe"
            })
        foreach ($child in $children) {
            & taskkill.exe /F /T /PID $child.ProcessId 2>$null
        }
    } catch {
        # Cleanup is best effort; WinSW still owns the runner process.
    }
}

# Windows PowerShell 5.1 may expose Console.CancelKeyPress as $null in the
# WinSW session-0 service context. Guard the CLR event before binding it.
$cancelEvent = [Console]::CancelKeyPress
if ($null -ne $cancelEvent) {
    $cancelEvent.AddHandler({ Stop-Child })
}
$exitCode = 1
trap { Stop-Child; break }
try {
    & $Updater run `
        --install-root $ReleaseRoot `
        --entrypoint bin/knoa-node.cmd `
        -- `
        --config $ConfigPath
    $exitCode = $LASTEXITCODE
} finally {
    Stop-Child
}
exit $exitCode
