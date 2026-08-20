[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$PythonExecutable,
    [Parameter(Mandatory = $true)][string]$TokenFile
)

$ErrorActionPreference = "Stop"
$env:KNOA_DESKTOP_COMPANION_TOKEN_FILE = $TokenFile
& $PythonExecutable -m knoa_platform.desktop_companion --token-file $TokenFile
exit $LASTEXITCODE
