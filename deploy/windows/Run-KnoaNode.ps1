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

# Keep Python as a direct, owned child of this runner.  WinSW stops this
# PowerShell process first; owning the child lets us tear down the complete
# Python/uvicorn tree instead of leaving a process behind with port 9530 open.
$psi = New-Object System.Diagnostics.ProcessStartInfo
$psi.FileName = $PythonExecutable
$psi.Arguments = "-m knoa_platform.service --config `"$ConfigPath`""
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
