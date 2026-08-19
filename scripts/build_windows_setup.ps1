[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$BundlePath,
    [Parameter(Mandatory = $true)][string]$UpdaterPath,
    [Parameter(Mandatory = $true)][string]$TrustStorePath,
    [Parameter(Mandatory = $true)][string]$ProductVersion,
    [Parameter(Mandatory = $true)][string]$OutputDirectory,
    [string]$Iscc = "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe"
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$definition = Join-Path $root "deploy\product\windows\KnoaSetup.iss"
if (-not (Test-Path -LiteralPath $Iscc)) { throw "Inno Setup 6 compiler was not found: $Iscc" }
New-Item -ItemType Directory -Force -Path $OutputDirectory | Out-Null
& $Iscc `
    "/DBundlePath=$((Resolve-Path $BundlePath).Path)" `
    "/DUpdaterPath=$((Resolve-Path $UpdaterPath).Path)" `
    "/DTrustStorePath=$((Resolve-Path $TrustStorePath).Path)" `
    "/DProductVersion=$ProductVersion" `
    "/O$((Resolve-Path $OutputDirectory).Path)" `
    $definition
if ($LASTEXITCODE -ne 0) { throw "Knoa Windows Setup build failed" }

