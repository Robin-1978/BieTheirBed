[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Updater,
    [Parameter(Mandatory = $true)][string]$ReleaseRoot,
    [Parameter(Mandatory = $true)][string]$TokenFile
)

$ErrorActionPreference = "Stop"
$env:KNOA_DESKTOP_COMPANION_TOKEN_FILE = $TokenFile

# This user-session launcher is also the update supervisor.  Services always
# resolve the active immutable release through knoa-update; the Companion must
# do the same and restart itself when Console activation changes state.json.
$activeRelease = ""
$child = $null
try {
    while ($true) {
        $current = (& $Updater current --install-root $ReleaseRoot).Trim()
        if ($LASTEXITCODE -ne 0 -or -not $current) {
            Start-Sleep -Seconds 5
            continue
        }
        if (-not $child -or $child.HasExited -or $current -ne $activeRelease) {
            if ($child -and -not $child.HasExited) {
                Stop-Process -Id $child.Id -Force -ErrorAction SilentlyContinue
                $child.WaitForExit(5000) | Out-Null
            }
            $arguments = 'run --install-root "{0}" --entrypoint bin/knoa-desktop-companion.cmd -- --token-file "{1}"' -f `
                $ReleaseRoot, $TokenFile
            $child = Start-Process -FilePath $Updater -ArgumentList $arguments -WindowStyle Hidden -PassThru
            $activeRelease = $current
        }
        Start-Sleep -Seconds 3
    }
} finally {
    if ($child -and -not $child.HasExited) {
        Stop-Process -Id $child.Id -Force -ErrorAction SilentlyContinue
    }
}
