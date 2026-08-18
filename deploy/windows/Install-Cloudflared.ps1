[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$CloudflaredExecutable,
    [Parameter(Mandatory = $true)][string]$WinSWExecutable,
    [Parameter(Mandatory = $true)][string[]]$TunnelNames,
    [Parameter(Mandatory = $true)][string[]]$TunnelTokenFiles,
    [string]$InstallRoot = "$env:ProgramData\Cloudflared"
)

$ErrorActionPreference = "Stop"

function Assert-Administrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = [Security.Principal.WindowsPrincipal]::new($identity)
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw "Run Install-Cloudflared.ps1 from an elevated PowerShell window"
    }
}

function Protect-CloudflaredPath([string]$Path) {
    New-Item -ItemType Directory -Force -Path $Path | Out-Null
    $currentUser = [Security.Principal.WindowsIdentity]::GetCurrent().Name
    & icacls.exe $Path /inheritance:r | Out-Null
    & icacls.exe $Path /grant:r `
        "*S-1-5-18:(OI)(CI)F" `
        "*S-1-5-32-544:(OI)(CI)F" `
        "${currentUser}:(OI)(CI)F" /T /C | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "Could not apply the required NTFS ACL to $Path"
    }
}

function Write-Utf8NoBom([string]$Path, [string]$Content) {
    $encoding = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($Path, $Content, $encoding)
}

function Escape-Xml([string]$Value) {
    return [Security.SecurityElement]::Escape($Value)
}

Assert-Administrator
if ($TunnelNames.Count -ne $TunnelTokenFiles.Count -or $TunnelNames.Count -lt 1) {
    throw "TunnelNames and TunnelTokenFiles must contain the same non-zero number of entries"
}
if ((Get-Service -Name "cloudflared" -ErrorAction SilentlyContinue)) {
    throw "The native cloudflared service is installed. Uninstall it before installing named WinSW Tunnel services."
}

$resolvedCloudflared = (Resolve-Path -LiteralPath $CloudflaredExecutable).Path
$resolvedWinSW = (Resolve-Path -LiteralPath $WinSWExecutable).Path
$binRoot = Join-Path $InstallRoot "bin"
$secretRoot = Join-Path $InstallRoot "Secrets"
$serviceRoot = Join-Path $InstallRoot "Services"
$logRoot = Join-Path $InstallRoot "Logs"
Protect-CloudflaredPath $InstallRoot
New-Item -ItemType Directory -Force -Path $binRoot, $secretRoot, $serviceRoot, $logRoot | Out-Null
$installedCloudflared = Join-Path $binRoot "cloudflared.exe"

$seen = @{}
for ($index = 0; $index -lt $TunnelNames.Count; $index++) {
    $name = $TunnelNames[$index].Trim()
    if ($name -notmatch '^[A-Za-z][A-Za-z0-9_-]{0,31}$') {
        throw "Tunnel name '$name' must contain 1-32 safe characters and start with a letter"
    }
    $key = $name.ToLowerInvariant()
    if ($seen.ContainsKey($key)) { throw "Tunnel names must be unique" }
    $seen[$key] = $true

    $serviceId = "Cloudflared-$name"
    $wrapper = Join-Path $serviceRoot "$serviceId\$serviceId.exe"
    if ((Test-Path -LiteralPath $wrapper) -and
        (Get-Service -Name $serviceId -ErrorAction SilentlyContinue)) {
        & $wrapper stop | Out-Null
        & $wrapper uninstall | Out-Null
    }
}
Copy-Item -Force $resolvedCloudflared $installedCloudflared

for ($index = 0; $index -lt $TunnelNames.Count; $index++) {
    $name = $TunnelNames[$index].Trim()
    $key = $name.ToLowerInvariant()
    $sourceToken = (Resolve-Path -LiteralPath $TunnelTokenFiles[$index]).Path
    $token = (Get-Content -LiteralPath $sourceToken -Raw).Trim()
    if ($token.Length -lt 32 -or $token -match '\s') {
        throw "Cloudflare Tunnel token '$name' is invalid"
    }
    $tokenPath = Join-Path $secretRoot "$key.token"
    try {
        Set-Content -LiteralPath $tokenPath -Value $token -NoNewline -Encoding ASCII
    } finally {
        $token = $null
    }

    $serviceId = "Cloudflared-$name"
    $serviceDirectory = Join-Path $serviceRoot $serviceId
    $wrapper = Join-Path $serviceDirectory "$serviceId.exe"
    $configuration = Join-Path $serviceDirectory "$serviceId.xml"
    New-Item -ItemType Directory -Force -Path $serviceDirectory | Out-Null
    Copy-Item -Force $resolvedWinSW $wrapper

    $arguments = "--no-autoupdate tunnel run --token-file `"$tokenPath`""
    $xml = @"
<service>
  <id>$(Escape-Xml $serviceId)</id>
  <name>Cloudflared Tunnel $(Escape-Xml $name)</name>
  <description>Cloudflare connector for the $([Security.SecurityElement]::Escape($name)) Tunnel</description>
  <executable>$(Escape-Xml $installedCloudflared)</executable>
  <arguments>$(Escape-Xml $arguments)</arguments>
  <workingdirectory>$(Escape-Xml $InstallRoot)</workingdirectory>
  <startmode>Automatic</startmode>
  <delayedAutoStart>true</delayedAutoStart>
  <onfailure action="restart" delay="10 sec" />
  <stoptimeout>30 sec</stoptimeout>
  <logpath>$(Escape-Xml (Join-Path $logRoot $name))</logpath>
  <log mode="roll-by-size">
    <sizeThreshold>10240</sizeThreshold>
    <keepFiles>8</keepFiles>
  </log>
</service>
"@
    Write-Utf8NoBom $configuration $xml
    & $wrapper install
    if ($LASTEXITCODE -ne 0) { throw "Could not install $serviceId" }
    & $wrapper start
    if ($LASTEXITCODE -ne 0) { throw "Could not start $serviceId" }
}

Protect-CloudflaredPath $InstallRoot
Write-Host "Installed $($TunnelNames.Count) independent Cloudflare Tunnel services."
