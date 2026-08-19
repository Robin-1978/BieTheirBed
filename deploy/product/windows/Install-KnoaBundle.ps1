[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][ValidateSet("hub", "node", "all")][string]$Role,
    [Parameter(Mandatory = $true)][string]$BundlePath,
    [Parameter(Mandatory = $true)][string]$TrustStorePath,
    [Parameter(Mandatory = $true)][string]$UpdaterPath,
    [string]$InstallRoot = "$env:ProgramFiles\Knoa",
    [string]$DataRoot = "$env:ProgramData\Knoa",
    [string]$HubPublicUrl = "https://knoa.tinydotdot.com"
)

$ErrorActionPreference = "Stop"

function Assert-Administrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw "Run this installer from an elevated PowerShell window"
    }
}

function New-Secret {
    $bytes = New-Object byte[] 48
    $rng = [Security.Cryptography.RandomNumberGenerator]::Create()
    try { $rng.GetBytes($bytes) } finally { $rng.Dispose() }
    return [Convert]::ToBase64String($bytes).TrimEnd("=").Replace("+", "-").Replace("/", "_")
}

function Write-Utf8NoBom([string]$Path, [string]$Content) {
    [IO.File]::WriteAllText($Path, $Content, (New-Object Text.UTF8Encoding($false)))
}

function Stop-RoleServices {
    param([string[]]$ServiceNames)
    foreach ($name in $ServiceNames) {
        $service = Get-Service -Name $name -ErrorAction SilentlyContinue
        if ($service -and $service.Status -ne "Stopped") {
            Stop-Service -Name $name -Force
            $service.WaitForStatus("Stopped", [TimeSpan]::FromSeconds(30))
        }
    }
}

function Start-RoleServices {
    param([string[]]$ServiceNames)
    foreach ($name in $ServiceNames) {
        Start-Service -Name $name
        (Get-Service -Name $name).WaitForStatus("Running", [TimeSpan]::FromSeconds(30))
    }
}

function Wait-Health([string]$Uri) {
    $deadline = [DateTime]::UtcNow.AddSeconds(45)
    do {
        try {
            $response = Invoke-WebRequest -UseBasicParsing -Uri $Uri -TimeoutSec 5
            if ($response.StatusCode -eq 200) { return }
        } catch {
            Start-Sleep -Seconds 1
        }
    } while ([DateTime]::UtcNow -lt $deadline)
    throw "Knoa service health check failed: $Uri"
}

function Install-WinSWService {
    param(
        [string]$ServiceId,
        [string]$DisplayName,
        [string]$Arguments,
        [string]$WinSWSource,
        [string]$ServiceRoot,
        [string]$LogRoot
    )
    $root = Join-Path $ServiceRoot $ServiceId
    New-Item -ItemType Directory -Force -Path $root | Out-Null
    $wrapper = Join-Path $root "$ServiceId.exe"
    Copy-Item -LiteralPath $WinSWSource -Destination $wrapper -Force
    $xml = @"
<service>
  <id>$ServiceId</id>
  <name>$DisplayName</name>
  <description>$DisplayName</description>
  <executable>powershell.exe</executable>
  <arguments>$Arguments</arguments>
  <logpath>$LogRoot</logpath>
  <log mode="roll-by-size-time"><sizeThreshold>10485760</sizeThreshold><pattern>yyyyMMdd</pattern><autoRollAtTime>00:00:00</autoRollAtTime><zipOlderThanNumDays>7</zipOlderThanNumDays></log>
  <startmode>Automatic</startmode>
  <stoptimeout>30sec</stoptimeout>
  <stopparentprocessfirst>true</stopparentprocessfirst>
  <onfailure action="restart" delay="5 sec" />
</service>
"@
    Write-Utf8NoBom (Join-Path $root "$ServiceId.xml") $xml
    if (-not (Get-Service -Name $ServiceId -ErrorAction SilentlyContinue)) {
        & $wrapper install
        if ($LASTEXITCODE -ne 0) { throw "Could not install $ServiceId" }
    }
}

Assert-Administrator
$bundle = (Resolve-Path -LiteralPath $BundlePath).Path
$trust = (Resolve-Path -LiteralPath $TrustStorePath).Path
$incomingUpdater = (Resolve-Path -LiteralPath $UpdaterPath).Path
$releaseRoot = Join-Path $InstallRoot "releases"
$binRoot = Join-Path $InstallRoot "bin"
$serviceRoot = Join-Path $DataRoot "Services"
$scriptRoot = Join-Path $DataRoot "Scripts"
$configRoot = Join-Path $DataRoot "Config"
$secretRoot = Join-Path $DataRoot "Secrets"
$logRoot = Join-Path $DataRoot "Logs"
$hubRoot = Join-Path $DataRoot "HostedHub"
$nodeRoot = Join-Path $DataRoot "Node"
$workspaceRoot = Join-Path $DataRoot "Workspace"
$roleFile = Join-Path $configRoot "product-role.txt"
$staging = Join-Path $InstallRoot (".incoming." + [Guid]::NewGuid().ToString("N"))
$targetArch = if ($env:PROCESSOR_ARCHITECTURE -eq "ARM64") { "aarch64" } else { "x86_64" }
$services = @()
if ($Role -in @("hub", "all")) { $services += "KnoaHostedHub" }
if ($Role -in @("node", "all")) { $services += "KnoaNode" }

New-Item -ItemType Directory -Force -Path $InstallRoot, $releaseRoot, $binRoot, $serviceRoot, $scriptRoot, $configRoot, $secretRoot, $logRoot | Out-Null
& icacls.exe $DataRoot /inheritance:r /grant:r "SYSTEM:(OI)(CI)F" "Administrators:(OI)(CI)F" | Out-Null
if ($LASTEXITCODE -ne 0) { throw "Could not protect the Knoa data root ACL" }
if (Test-Path -LiteralPath $roleFile) {
    $installedRole = (Get-Content -LiteralPath $roleFile -Raw).Trim()
    if ($installedRole -ne $Role) {
        throw "Installed product role is '$installedRole'; role changes require an explicit migration"
    }
}
try {
    & $incomingUpdater install --archive $bundle --staging $staging --trust-store $trust --kind product --role $Role --target-os windows --target-arch $targetArch --install-root $releaseRoot --health-entrypoint bin/knoa-health.cmd
    if ($LASTEXITCODE -ne 0) { throw "Signed Knoa Bundle installation failed" }
} finally {
    Remove-Item -LiteralPath $staging -Recurse -Force -ErrorAction SilentlyContinue
}

$installedUpdater = Join-Path $binRoot "knoa-update.exe"
try {
    $current = (& $incomingUpdater current --install-root $releaseRoot).Trim()
    if ($LASTEXITCODE -ne 0 -or -not $current) { throw "Could not resolve the active Knoa Release" }
    $winsw = Join-Path $current "service\WinSW.exe"
    if (-not (Test-Path -LiteralPath $winsw)) { throw "The Windows Bundle does not contain service/WinSW.exe" }

    Stop-RoleServices $services
    Copy-Item -LiteralPath $incomingUpdater -Destination $installedUpdater -Force
    Copy-Item -LiteralPath (Join-Path $current "install\Run-KnoaHub.ps1") -Destination (Join-Path $scriptRoot "Run-KnoaHub.ps1") -Force
    Copy-Item -LiteralPath (Join-Path $current "install\Run-KnoaNode.ps1") -Destination (Join-Path $scriptRoot "Run-KnoaNode.ps1") -Force

    if ($Role -in @("hub", "all")) {
        New-Item -ItemType Directory -Force -Path $hubRoot | Out-Null
        foreach ($name in @("hub-bootstrap.token", "hub-release-publisher.token")) {
            $path = Join-Path $secretRoot $name
            if (-not (Test-Path -LiteralPath $path)) { Write-Utf8NoBom $path (New-Secret) }
        }
        $hubArguments = '-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "{0}" -Updater "{1}" -ReleaseRoot "{2}" -HubRoot "{3}" -BootstrapTokenFile "{4}" -ReleasePublishTokenFile "{5}"' -f `
            (Join-Path $scriptRoot "Run-KnoaHub.ps1"), $installedUpdater, $releaseRoot, $hubRoot, (Join-Path $secretRoot "hub-bootstrap.token"), (Join-Path $secretRoot "hub-release-publisher.token")
        Install-WinSWService "KnoaHostedHub" "Knoa Hosted Hub" $hubArguments $winsw $serviceRoot (Join-Path $logRoot "Hub")
    }
    if ($Role -in @("node", "all")) {
        New-Item -ItemType Directory -Force -Path $nodeRoot, $workspaceRoot | Out-Null
        $nodeConfig = Join-Path $configRoot "node.yaml"
        if (-not (Test-Path -LiteralPath $nodeConfig)) {
            $config = "runtime_root: `"$nodeRoot`"`nworking_directory: `"$workspaceRoot`"`nservice_host: `"127.0.0.1`"`nservice_port: 9527`ngateway_enabled: true`ngateway_host: `"127.0.0.1`"`ngateway_port: 9531`ngateway_remote_enabled: false`ncapability_mcp_host: `"127.0.0.1`"`ncapability_mcp_port: 9530`n"
            Write-Utf8NoBom $nodeConfig $config
        }
        $nodeArguments = '-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "{0}" -Updater "{1}" -ReleaseRoot "{2}" -NodeRoot "{3}" -ConfigPath "{4}"' -f `
            (Join-Path $scriptRoot "Run-KnoaNode.ps1"), $installedUpdater, $releaseRoot, $nodeRoot, $nodeConfig
        Install-WinSWService "KnoaNode" "Knoa Node" $nodeArguments $winsw $serviceRoot (Join-Path $logRoot "Node")
    }

    Start-RoleServices $services
    if ($Role -in @("hub", "all")) { Wait-Health "http://127.0.0.1:9529/health" }
    if ($Role -in @("node", "all")) { Wait-Health "http://127.0.0.1:9531/health" }
} catch {
    $failure = $_
    Stop-RoleServices $services
    $recoveryUpdater = if (Test-Path -LiteralPath $installedUpdater) { $installedUpdater } else { $incomingUpdater }
    & $recoveryUpdater reject --install-root $releaseRoot --health-entrypoint bin/knoa-health.cmd
    if ($LASTEXITCODE -eq 0) {
        & $recoveryUpdater current --install-root $releaseRoot 2>$null | Out-Null
        if ($LASTEXITCODE -eq 0) {
            try { Start-RoleServices $services } catch { }
        }
    }
    throw $failure
}

Write-Utf8NoBom $roleFile $Role
Write-Host "Installed and verified Knoa $Role Bundle. Persistent data remains under $DataRoot."
if ($Role -in @("hub", "all")) { Write-Host "Hub: $HubPublicUrl" }
if ($Role -in @("node", "all")) { Write-Host "Node Console/Gateway: http://127.0.0.1:9531" }
