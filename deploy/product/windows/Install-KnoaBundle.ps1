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

function Get-ListeningPids([int[]]$Ports) {
    $owners = @{}
    foreach ($port in $Ports) { $owners[$port] = @() }
    foreach ($connection in (Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue)) {
        $port = [int]$connection.LocalPort
        if ($owners.ContainsKey($port)) { $owners[$port] += [int]$connection.OwningProcess }
    }
    return $owners
}

function Stop-KnoaPortOwners([int[]]$Ports) {
    foreach ($entry in (Get-ListeningPids $Ports).GetEnumerator()) {
        foreach ($ownerPid in ($entry.Value | Select-Object -Unique)) {
            if ($ownerPid -le 0 -or $ownerPid -eq $PID) { continue }
            $process = Get-CimInstance Win32_Process -Filter "ProcessId=$ownerPid" -ErrorAction SilentlyContinue
            $commandLine = [string]$process.CommandLine
            if ($commandLine -and $commandLine -notmatch "(?i)knoa|Run-Knoa") {
                throw "Foreign process $ownerPid owns Knoa port $($entry.Key): $commandLine"
            }
            & taskkill.exe /F /T /PID $ownerPid 2>$null | Out-Null
        }
    }
}

function Wait-KnoaPortsReleased([int[]]$Ports, [int]$TimeoutSeconds = 30) {
    $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    do {
        Stop-KnoaPortOwners $Ports
        $owners = Get-ListeningPids $Ports
        if (($owners.Values | ForEach-Object { $_.Count } | Measure-Object -Sum).Sum -eq 0) { return }
        Start-Sleep -Milliseconds 500
    } while ([DateTime]::UtcNow -lt $deadline)
    $detail = ($owners.GetEnumerator() | Where-Object { $_.Value.Count } | ForEach-Object { "$($_.Key):$($_.Value -join ',')" }) -join '; '
    throw "Knoa ports were not released before installation: $detail"
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

function Install-KnoaP2PFirewallRule([string]$ProgramPath) {
    $ruleName = "KnoaNodeWebRtcP2P"
    Get-NetFirewallRule -Name $ruleName -ErrorAction SilentlyContinue | `
        Remove-NetFirewallRule -ErrorAction SilentlyContinue
    New-NetFirewallRule `
        -Name $ruleName `
        -DisplayName "Knoa Node WebRTC P2P" `
        -Description "Allow authenticated WebRTC ICE/UDP traffic for Knoa Node" `
        -Direction Inbound `
        -Action Allow `
        -Enabled True `
        -Profile Any `
        -Protocol UDP `
        -Program $ProgramPath | Out-Null
}

function Install-KnoaMdnsFirewallRules {
    $mdnsRuleName = "KnoaNodeMdns"
    Get-NetFirewallRule -Name $mdnsRuleName -ErrorAction SilentlyContinue | `
        Remove-NetFirewallRule -ErrorAction SilentlyContinue
    New-NetFirewallRule `
        -Name $mdnsRuleName `
        -DisplayName "Knoa Node mDNS Discovery" `
        -Description "Allow Knoa Node mDNS discovery traffic on UDP 5353" `
        -Direction Inbound `
        -Action Allow `
        -Enabled True `
        -Profile Any `
        -Protocol UDP `
        -LocalPort 5353 | Out-Null

    $gatewayRuleName = "KnoaNodeLanGateway"
    Get-NetFirewallRule -Name $gatewayRuleName -ErrorAction SilentlyContinue | `
        Remove-NetFirewallRule -ErrorAction SilentlyContinue
    New-NetFirewallRule `
        -Name $gatewayRuleName `
        -DisplayName "Knoa Node LAN Gateway" `
        -Description "Allow authenticated Knoa Node LAN gateway traffic on TCP 9541" `
        -Direction Inbound `
        -Action Allow `
        -Enabled True `
        -Profile Any `
        -Protocol TCP `
        -LocalPort 9541 | Out-Null
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
$hostState = Join-Path $configRoot "host-state.json"
$lifecycleToken = Join-Path $secretRoot "lifecycle.token"
$desktopRoot = Join-Path $DataRoot "Desktop"
$desktopToken = Join-Path $desktopRoot "companion.token"
$lifecycleTrust = Join-Path $configRoot "release-trust.json"
$incomingRoot = Join-Path $DataRoot "Incoming"
$staging = Join-Path $InstallRoot (".incoming." + [Guid]::NewGuid().ToString("N"))
$targetArch = if ($env:PROCESSOR_ARCHITECTURE -eq "ARM64") { "aarch64" } else { "x86_64" }
$services = @()
if ($Role -in @("hub", "all")) { $services += "KnoaHostedHub" }
if ($Role -in @("node", "all")) { $services += "KnoaNode" }

New-Item -ItemType Directory -Force -Path $InstallRoot, $releaseRoot, $binRoot, $serviceRoot, $scriptRoot, $configRoot, $secretRoot, $logRoot, $incomingRoot, $desktopRoot | Out-Null
& icacls.exe $DataRoot /inheritance:r /grant:r "SYSTEM:(OI)(CI)F" "Administrators:(OI)(CI)F" | Out-Null
if ($LASTEXITCODE -ne 0) { throw "Could not protect the Knoa data root ACL" }
try {
    & $incomingUpdater install --archive $bundle --staging $staging --trust-store $trust --kind product --role all --target-os windows --target-arch $targetArch --install-root $releaseRoot --health-entrypoint bin/knoa-health.cmd
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
    if ($Role -in @("node", "all")) {
        Install-KnoaP2PFirewallRule (Join-Path $current "runtime\python.exe")
        Install-KnoaMdnsFirewallRules
    }

    Stop-RoleServices @("KnoaHostLifecycle", "KnoaHostedHub", "KnoaNode")
    $criticalPorts = @(9533)
    if ($Role -in @("hub", "all")) { $criticalPorts += 9529, 9532 }
    if ($Role -in @("node", "all")) { $criticalPorts += 9527, 9530, 9531, 9541 }
    Wait-KnoaPortsReleased $criticalPorts
    Copy-Item -LiteralPath $incomingUpdater -Destination $installedUpdater -Force
    Copy-Item -LiteralPath (Join-Path $current "install\Run-KnoaHub.ps1") -Destination (Join-Path $scriptRoot "Run-KnoaHub.ps1") -Force
    Copy-Item -LiteralPath (Join-Path $current "install\Run-KnoaNode.ps1") -Destination (Join-Path $scriptRoot "Run-KnoaNode.ps1") -Force
    Copy-Item -LiteralPath (Join-Path $current "install\Run-KnoaHostLifecycle.ps1") -Destination (Join-Path $scriptRoot "Run-KnoaHostLifecycle.ps1") -Force
    Copy-Item -LiteralPath (Join-Path $current "install\Run-KnoaDesktopCompanion.ps1") -Destination (Join-Path $scriptRoot "Run-KnoaDesktopCompanion.ps1") -Force

    New-Item -ItemType Directory -Force -Path $hubRoot, $nodeRoot, $workspaceRoot | Out-Null
    foreach ($name in @("hub-bootstrap.token", "hub-release-publisher.token", "lifecycle.token")) {
            $path = Join-Path $secretRoot $name
            if (-not (Test-Path -LiteralPath $path)) { Write-Utf8NoBom $path (New-Secret) }
    }
    if (-not (Test-Path -LiteralPath $desktopToken)) { Write-Utf8NoBom $desktopToken (New-Secret) }
    & icacls.exe $desktopRoot /inheritance:r /grant:r "SYSTEM:(OI)(CI)F" "Administrators:(OI)(CI)F" "*S-1-5-32-545:(OI)(CI)RX" | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Could not configure the Desktop Companion ACL" }
    & icacls.exe $desktopToken /inheritance:r /grant:r "SYSTEM:F" "Administrators:F" "*S-1-5-32-545:R" | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Could not protect the Desktop Companion token" }
    Copy-Item -LiteralPath $trust -Destination $lifecycleTrust -Force
    $nodeConfig = Join-Path $configRoot "node.yaml"
    if (-not (Test-Path -LiteralPath $nodeConfig)) {
        $config = "runtime_root: `"$nodeRoot`"`nworking_directory: `"$workspaceRoot`"`nservice_host: `"127.0.0.1`"`nservice_port: 9527`ngateway_enabled: true`ngateway_host: `"127.0.0.1`"`ngateway_port: 9531`ngateway_lan_enabled: true`ngateway_lan_host: `"0.0.0.0`"`ngateway_lan_port: 9541`ngateway_remote_enabled: false`ncapability_mcp_host: `"127.0.0.1`"`ncapability_mcp_port: 9530`n"
        Write-Utf8NoBom $nodeConfig $config
    }
    $roles = if ($Role -eq "all") { @("hub", "node") } else { @($Role) }
    Write-Utf8NoBom $hostState ((@{ schema_version = 1; installed_roles = $roles } | ConvertTo-Json -Compress))
    $hubArguments = '-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "{0}" -Updater "{1}" -ReleaseRoot "{2}" -HubRoot "{3}" -BootstrapTokenFile "{4}" -ReleasePublishTokenFile "{5}" -LifecycleTokenFile "{6}" -LifecycleIncomingRoot "{7}" -PublicUrl "{8}"' -f `
        (Join-Path $scriptRoot "Run-KnoaHub.ps1"), $installedUpdater, $releaseRoot, $hubRoot, (Join-Path $secretRoot "hub-bootstrap.token"), (Join-Path $secretRoot "hub-release-publisher.token"), $lifecycleToken, $incomingRoot, $HubPublicUrl
    $nodeArguments = '-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "{0}" -Updater "{1}" -ReleaseRoot "{2}" -NodeRoot "{3}" -ConfigPath "{4}" -LifecycleTokenFile "{5}" -LifecycleIncomingRoot "{6}" -DesktopCompanionTokenFile "{7}"' -f `
        (Join-Path $scriptRoot "Run-KnoaNode.ps1"), $installedUpdater, $releaseRoot, $nodeRoot, $nodeConfig, $lifecycleToken, $incomingRoot, $desktopToken
    $lifecycleArguments = '-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "{0}" -Updater "{1}" -ReleaseRoot "{2}" -TrustStore "{3}" -StateFile "{4}" -IncomingRoot "{5}" -TokenFile "{6}"' -f `
        (Join-Path $scriptRoot "Run-KnoaHostLifecycle.ps1"), $installedUpdater, $releaseRoot, $lifecycleTrust, $hostState, $incomingRoot, $lifecycleToken
    Install-WinSWService "KnoaHostedHub" "Knoa Hosted Hub" $hubArguments $winsw $serviceRoot (Join-Path $logRoot "Hub")
    Install-WinSWService "KnoaNode" "Knoa Node" $nodeArguments $winsw $serviceRoot (Join-Path $logRoot "Node")
    Install-WinSWService "KnoaHostLifecycle" "Knoa Host Lifecycle" $lifecycleArguments $winsw $serviceRoot (Join-Path $logRoot "Lifecycle")

    if ($Role -in @("node", "all")) {
        $companionCommand = 'powershell.exe -NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File "{0}" -Updater "{1}" -ReleaseRoot "{2}" -TokenFile "{3}"' -f `
            (Join-Path $scriptRoot "Run-KnoaDesktopCompanion.ps1"), $installedUpdater, $releaseRoot, $desktopToken
        & reg.exe add "HKLM\Software\Microsoft\Windows\CurrentVersion\Run" /v "KnoaDesktopCompanion" /t REG_SZ /d $companionCommand /f | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "Could not register the Desktop Companion login launcher" }
        $runningCompanion = Get-CimInstance Win32_Process -Filter "Name = 'powershell.exe'" -ErrorAction SilentlyContinue | `
            Where-Object { $_.CommandLine -like "*Run-KnoaDesktopCompanion.ps1*" }
        if (-not $runningCompanion) {
            Start-Process -FilePath "powershell.exe" -ArgumentList ($companionCommand.Substring("powershell.exe ".Length)) -WindowStyle Hidden | Out-Null
        }
    } else {
        & reg.exe delete "HKLM\Software\Microsoft\Windows\CurrentVersion\Run" /v "KnoaDesktopCompanion" /f 2>$null | Out-Null
    }

    Set-Service -Name KnoaHostedHub -StartupType Disabled
    Set-Service -Name KnoaNode -StartupType Disabled
    Set-Service -Name KnoaHostLifecycle -StartupType Automatic
    if ($Role -in @("hub", "all")) { Set-Service -Name KnoaHostedHub -StartupType Automatic }
    if ($Role -in @("node", "all")) { Set-Service -Name KnoaNode -StartupType Automatic }

    Start-RoleServices @("KnoaHostLifecycle")
    Start-RoleServices $services
    if ($Role -in @("hub", "all")) { Wait-Health "http://127.0.0.1:9529/health" }
    if ($Role -in @("node", "all")) { Wait-Health "http://127.0.0.1:9531/health" }
} catch {
    $failure = $_
    Stop-RoleServices (@("KnoaHostLifecycle") + $services)
    $recoveryUpdater = if (Test-Path -LiteralPath $installedUpdater) { $installedUpdater } else { $incomingUpdater }
    & $recoveryUpdater reject --install-root $releaseRoot --health-entrypoint bin/knoa-health.cmd
    if ($LASTEXITCODE -eq 0) {
        & $recoveryUpdater current --install-root $releaseRoot 2>$null | Out-Null
        if ($LASTEXITCODE -eq 0) {
            try {
                Start-RoleServices @("KnoaHostLifecycle")
                Start-RoleServices $services
            } catch { }
        }
    }
    throw $failure
}

Write-Host "Installed and verified the universal Knoa Host Bundle with active roles: $Role. Persistent data remains under $DataRoot."
if ($Role -in @("hub", "all")) { Write-Host "Hub: $HubPublicUrl" }
if ($Role -in @("node", "all")) { Write-Host "Node Console/Gateway: http://127.0.0.1:9531" }
if ($Role -in @("node", "all")) { Write-Host "Desktop Companion is running and will start automatically at Windows sign-in." }
