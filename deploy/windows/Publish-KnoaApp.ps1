[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$ApkPath,
    [string]$HubRoot = "$env:ProgramData\Knoa\HostedHub",
    [string]$PythonExecutable = "$env:ProgramData\Knoa\Runtime\venv\Scripts\python.exe",
    [string]$HubPublicUrl = "https://knoa.tinydotdot.com",
    [string]$AppMetadataPath = "C:\knoa\apps\knoa-mobile\app.json",
    [int]$MinVersionCode = 1,
    [string]$VersionName = "",
    [int]$VersionCode = 0,
    [string]$Notes = ""
)

$ErrorActionPreference = "Stop"
$resolvedApk = (Resolve-Path -LiteralPath $ApkPath).Path
$resolvedPython = (Resolve-Path -LiteralPath $PythonExecutable).Path
if (-not (Test-Path -LiteralPath $HubRoot)) {
    throw "Hosted Hub root not found: $HubRoot"
}
if (-not $VersionName -and $VersionCode -eq 0 -and (Test-Path -LiteralPath $AppMetadataPath)) {
    $appMetadata = Get-Content -LiteralPath $AppMetadataPath -Raw | ConvertFrom-Json
    $VersionName = [string]$appMetadata.expo.version
    $VersionCode = [int]$appMetadata.expo.android.versionCode
}

$publishArguments = @(
    "-m", "knoa_platform.hub.admin", "mobile-publish", $resolvedApk,
    "--root", $HubRoot,
    "--min-version-code", $MinVersionCode
)
if ($Notes) { $publishArguments += "--notes", $Notes }
if ($VersionName -or $VersionCode) {
    if (-not $VersionName -or $VersionCode -lt 1) {
        throw "VersionName and VersionCode must be supplied together"
    }
    $publishArguments += "--version-name", $VersionName
    $publishArguments += "--version-code", $VersionCode
}
& $resolvedPython @publishArguments
if ($LASTEXITCODE -ne 0) { throw "Could not publish the Knoa Android App" }

& $resolvedPython -m knoa_platform.hub.admin mobile-latest --root $HubRoot
if ($LASTEXITCODE -ne 0) { throw "Could not verify the published Knoa Android App" }

Write-Host "Stable App download: $($HubPublicUrl.TrimEnd('/'))/downloads/android/latest.apk"
