[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$ApkPath,
    [string]$HubRoot = "$env:ProgramData\Knoa\HostedHub",
    [string]$PythonExecutable = "$env:ProgramData\Knoa\Runtime\venv\Scripts\python.exe",
    [string]$HubPublicUrl = "https://knoa.tinydotdot.com",
    [string]$AppMetadataPath = "C:\knoa\apps\knoa-mobile\app.json",
    [string]$ReleaseMetadataPath = "",
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
if (-not $ReleaseMetadataPath) {
    $ReleaseMetadataPath = [IO.Path]::ChangeExtension($resolvedApk, ".release.json")
}
if (-not $VersionName -and $VersionCode -eq 0 -and (Test-Path -LiteralPath $ReleaseMetadataPath)) {
    $releaseMetadata = Get-Content -LiteralPath $ReleaseMetadataPath -Raw -Encoding UTF8 |
        ConvertFrom-Json
    if ([string]$releaseMetadata.platform -ne "android" -or
        [string]$releaseMetadata.package_id -ne "dev.knoa.mobile" -or
        [string]$releaseMetadata.file_name -ne [IO.Path]::GetFileName($resolvedApk) -or
        [int64]$releaseMetadata.size_bytes -ne (Get-Item -LiteralPath $resolvedApk).Length -or
        [string]$releaseMetadata.sha256 -ne (Get-FileHash -LiteralPath $resolvedApk -Algorithm SHA256).Hash.ToLowerInvariant()) {
        throw "Android release metadata does not match the APK"
    }
    $VersionName = [string]$releaseMetadata.version_name
    $VersionCode = [int]$releaseMetadata.version_code
    $MinVersionCode = [int]$releaseMetadata.min_supported_version_code
} elseif (-not $VersionName -and $VersionCode -eq 0 -and (Test-Path -LiteralPath $AppMetadataPath)) {
    $appMetadata = Get-Content -LiteralPath $AppMetadataPath -Raw -Encoding UTF8 |
        ConvertFrom-Json
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
