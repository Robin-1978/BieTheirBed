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

# Keep Python as a direct, owned child of this runner.  Start-Process is
# reliable in WinSW's session-0 service context, where Process.Start can
# return $null for a console child.
$workingDirectory = Join-Path (Split-Path -Parent $NodeRoot) "Workspace"
if (-not (Test-Path -LiteralPath $workingDirectory)) {
    $workingDirectory = Split-Path -Parent $ConfigPath
}
$proc = Start-Process -FilePath $PythonExecutable `
    -ArgumentList @("-m", "knoa_platform.service", "--config", $ConfigPath) `
    -PassThru -WindowStyle Hidden -WorkingDirectory $workingDirectory
if (-not $proc) { throw "Failed to launch Knoa Python runtime" }

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
