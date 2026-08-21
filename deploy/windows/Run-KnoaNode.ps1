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

# Use the call operator in the WinSW session-0 service context.  Unlike both
# Process.Start and Start-Process -WindowStyle Hidden, this reliably launches
# the console runtime.  The CIM fallback below identifies only this runner's
# Python child and recursively terminates it if Ctrl+C does not propagate.
$runnerPid = $PID

function Stop-Child {
    try {
        $children = @(Get-CimInstance Win32_Process `
            -Filter "ParentProcessId = $runnerPid" `
            -ErrorAction Stop | Where-Object {
                $_.Name -match '^python(?:w|3)?\.exe$' -and
                $_.CommandLine -match 'knoa_platform\.service'
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
    & $PythonExecutable -m knoa_platform.service --config $ConfigPath
    $exitCode = $LASTEXITCODE
} finally {
    Stop-Child
}
exit $exitCode
