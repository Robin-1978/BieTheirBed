[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$ApkPath,
    [string]$HubRoot = "$env:ProgramData\Knoa\HostedHub",
    [string]$PythonExecutable = "$env:ProgramData\Knoa\Runtime\venv\Scripts\python.exe",
    [string]$HubPublicUrl = "https://knoa.tinydotdot.com",
    [int]$MinVersionCode = 1,
    [string]$Notes = ""
)

$ErrorActionPreference = "Stop"
$resolvedApk = (Resolve-Path -LiteralPath $ApkPath).Path
$resolvedPython = (Resolve-Path -LiteralPath $PythonExecutable).Path
if (-not (Test-Path -LiteralPath $HubRoot)) {
    throw "Hosted Hub root not found: $HubRoot"
}

$publishArguments = @(
    "-m", "knoa_platform.hub.admin", "mobile-publish", $resolvedApk,
    "--root", $HubRoot,
    "--min-version-code", $MinVersionCode
)
if ($Notes) { $publishArguments += "--notes", $Notes }
& $resolvedPython @publishArguments
if ($LASTEXITCODE -ne 0) { throw "Could not publish the Knoa Android App" }

& $resolvedPython -m knoa_platform.hub.admin mobile-latest --root $HubRoot
if ($LASTEXITCODE -ne 0) { throw "Could not verify the published Knoa Android App" }

Write-Host "Stable App download: $($HubPublicUrl.TrimEnd('/'))/downloads/android/latest.apk"
