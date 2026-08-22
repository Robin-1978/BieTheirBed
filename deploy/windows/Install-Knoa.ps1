[CmdletBinding()]
param(
    [ValidateSet("all", "hub", "node")]
    [string]$Role = "all",
    [string]$WheelPath = "",
    [string]$SourcePath = "",
    [string]$ChannelSourcePath = "",
    [string]$WinSWExecutable = "",
    [string]$WheelhousePath = "",
    [string]$HubRoot = "$env:ProgramData\Knoa\HostedHub",
    [string]$NodeRoot = "$env:ProgramData\Knoa\Node",
    [string]$LegacyNodeRoot = "$env:LOCALAPPDATA\Knoa\Node",
    [string]$InstallRoot = "$env:ProgramData\Knoa\Runtime",
    [string]$HubPublicUrl = "https://knoa.tinydotdot.com",
    [string]$HubId = "hub_knoa_hosted",
    [string]$HostedBackupPath = "",
    [string]$BootstrapTokenSource = "",
    [string]$ReleasePublishTokenSource = "",
    [string]$PythonVersion = "3.14",
    [switch]$RecreateVenv,
    [switch]$SkipPairingQr,
    [int]$HubPort = 9529,
    [int]$NodeCorePort = 9527,
    [int]$NodeGatewayPort = 9531,
    [int]$NodeMcpPort = 9530
)

$ErrorActionPreference = "Stop"

function Assert-KnoaSourceReleaseVersion([string]$SourceRoot) {
    $versionFile = Join-Path $SourceRoot "src\knoa_platform\__init__.py"
    $manifestFile = Join-Path $SourceRoot "release\versions.json"
    if (-not (Test-Path -LiteralPath $versionFile)) {
        throw "Knoa source version file is missing: $versionFile"
    }
    if (-not (Test-Path -LiteralPath $manifestFile)) {
        throw "Knoa release version manifest is missing: $manifestFile. Pull the latest master before installing."
    }
    $sourceMatch = Select-String -LiteralPath $versionFile -Pattern '__version__\s*=\s*["'']([^"'']+)["'']' |
        Select-Object -First 1
    $manifest = Get-Content -LiteralPath $manifestFile -Raw -Encoding UTF8 | ConvertFrom-Json
    $declared = if ($sourceMatch -and $sourceMatch.Matches.Count) {
        $sourceMatch.Matches[0].Groups[1].Value
    } else { "" }
    $manifestVersion = [string]$manifest.platform_version
    if (-not $declared -or -not $manifestVersion -or $declared -ne $manifestVersion) {
        throw "Knoa source release metadata is inconsistent: source=$declared manifest=$manifestVersion"
    }
    Write-Host "Knoa source release version: $declared"
}
$installHub = ($Role -in @("all", "hub")) -or [bool](Get-Service -Name "KnoaHostedHub" -ErrorAction SilentlyContinue)
$installNode = ($Role -in @("all", "node")) -or [bool](Get-Service -Name "KnoaNode" -ErrorAction SilentlyContinue)
if (-not $installHub -and ($HostedBackupPath -or $BootstrapTokenSource -or $ReleasePublishTokenSource)) {
    throw "HostedBackupPath, BootstrapTokenSource and ReleasePublishTokenSource require -Role hub or -Role all"
}

function Assert-Administrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = [Security.Principal.WindowsPrincipal]::new($identity)
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw "Run Install-Knoa.ps1 from an elevated PowerShell window"
    }
}

function Protect-KnoaPath([string]$Path, [switch]$Recursive) {
    New-Item -ItemType Directory -Force -Path $Path | Out-Null
    $currentUser = [Security.Principal.WindowsIdentity]::GetCurrent().Name
    & icacls.exe $Path /inheritance:r | Out-Null
    $grantArguments = @(
        $Path,
        "/grant:r",
        "*S-1-5-18:(OI)(CI)F",
        "*S-1-5-32-544:(OI)(CI)F",
        "${currentUser}:(OI)(CI)F"
    )
    if ($Recursive) { $grantArguments += "/T", "/C" }
    & icacls.exe @grantArguments | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "Could not apply the required NTFS ACL to $Path"
    }
}

function New-RandomToken {
    $bytes = New-Object byte[] 48
    $generator = [Security.Cryptography.RandomNumberGenerator]::Create()
    try { $generator.GetBytes($bytes) } finally { $generator.Dispose() }
    return [Convert]::ToBase64String($bytes)
}

function Quote-Yaml([string]$Value) {
    return "'" + $Value.Replace("'", "''").Replace("\", "/") + "'"
}

function Write-Utf8NoBom([string]$Path, [string]$Content) {
    $encoding = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($Path, $Content, $encoding)
}

function Escape-Xml([string]$Value) {
    return [Security.SecurityElement]::Escape($Value)
}

function Remove-WinSWService([string]$ServiceId, [string]$WrapperPath) {
    if ((Test-Path -LiteralPath $WrapperPath) -and
        (Get-Service -Name $ServiceId -ErrorAction SilentlyContinue)) {
        & $WrapperPath stop | Out-Null
        & $WrapperPath uninstall | Out-Null
    }
}

function Wait-WinSWServiceDeleted([string]$ServiceId, [int]$TimeoutSeconds = 15) {
    # SCM keeps a service "marked for deletion" while any handle to it is
    # open (Services.msc, ServiceController objects). Re-registering the
    # same name in that window deadlocks or fails, so wait it out first.
    $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    while ([DateTime]::UtcNow -lt $deadline) {
        if (-not (Get-Service -Name $ServiceId -ErrorAction SilentlyContinue)) { return }
        Start-Sleep -Milliseconds 500
    }
    throw "Knoa service $ServiceId is still registered after uninstall; close Services.msc and retry"
}

function Stop-KnoaService([string]$ServiceId) {
    $service = Get-Service -Name $ServiceId -ErrorAction SilentlyContinue
    if ($service -and $service.Status -ne "Stopped") {
        Stop-Service -InputObject $service -Force
        $service.WaitForStatus(
            [System.ServiceProcess.ServiceControllerStatus]::Stopped,
            [TimeSpan]::FromSeconds(30)
        )
    }
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

function Install-WinSWService(
    [string]$ServiceId,
    [string]$Xml,
    [string]$SourceExecutable,
    [string]$ServicesRoot
) {
    $serviceDirectory = Join-Path $ServicesRoot $ServiceId
    $wrapper = Join-Path $serviceDirectory "$ServiceId.exe"
    $configuration = Join-Path $serviceDirectory "$ServiceId.xml"
    Write-Utf8NoBom $configuration $Xml
    Remove-WinSWService $ServiceId $wrapper
    Wait-WinSWServiceDeleted $ServiceId
    if ([IO.Path]::GetFullPath($SourceExecutable) -ne [IO.Path]::GetFullPath($wrapper)) {
        Copy-Item -Force $SourceExecutable $wrapper
    }
    & $wrapper install
    if ($LASTEXITCODE -ne 0) { throw "Could not install WinSW service $ServiceId" }
    & $wrapper start
    if ($LASTEXITCODE -ne 0) { throw "Could not start WinSW service $ServiceId" }
}

Assert-Administrator
# The user-facing updater and the source lifecycle broker can both decide to
# reinstall at the same time; concurrent installers deadlock on the venv and
# the SCM service database. Serialize every install through a global mutex.
$installerMutex = New-Object System.Threading.Mutex($false, "Global\KnoaWindowsInstaller")
try {
    $mutexAcquired = $installerMutex.WaitOne([TimeSpan]::Zero)
} catch [System.Threading.AbandonedMutexException] {
    $mutexAcquired = $true
}
if (-not $mutexAcquired) {
    throw "Another Knoa install or update is already running; wait for it to finish"
}
if (-not $WinSWExecutable) {
    $wrapperCandidates = @(
        "$env:ProgramData\Knoa\Services\KnoaHostedHub\KnoaHostedHub.exe",
        "$env:ProgramData\Knoa\Services\KnoaNode\KnoaNode.exe"
    )
    $existingWrapper = $wrapperCandidates |
        Where-Object { Test-Path -LiteralPath $_ } |
        Select-Object -First 1
    if ($existingWrapper -and (Test-Path -LiteralPath $existingWrapper)) {
        $WinSWExecutable = $existingWrapper
    } else {
        throw "WinSWExecutable is required for the first installation"
    }
}
$resolvedWinSW = (Resolve-Path -LiteralPath $WinSWExecutable).Path
$selectedPorts = @()
if ($installHub) { $selectedPorts += $HubPort }
if ($installNode) { $selectedPorts += $NodeCorePort, $NodeGatewayPort, $NodeMcpPort }
$duplicatePorts = $selectedPorts |
    Group-Object |
    Where-Object { $_.Count -gt 1 }
if ($duplicatePorts) {
    throw "Hub, Node Core, Node Gateway and Node MCP ports must be distinct"
}

if ($WheelPath -and $SourcePath) {
    throw "Specify WheelPath or SourcePath, not both"
}
if ($WheelPath) {
    $resolvedPackage = (Resolve-Path -LiteralPath $WheelPath).Path
    $sourceInstall = $false
} else {
    if (-not $SourcePath) {
        $SourcePath = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..\..")).Path
    }
    $resolvedPackage = (Resolve-Path -LiteralPath $SourcePath).Path
    $sourceInstall = $true
}
if ($sourceInstall) {
    Assert-KnoaSourceReleaseVersion $resolvedPackage
    if (-not $ChannelSourcePath) { $ChannelSourcePath = $resolvedPackage }
    $ChannelSourcePath = (Resolve-Path -LiteralPath $ChannelSourcePath).Path
    if (-not (Test-Path -LiteralPath (Join-Path $ChannelSourcePath ".git"))) {
        throw "ChannelSourcePath must be a Git checkout"
    }
}
$baseRoot = Split-Path -Parent $InstallRoot
$configRoot = Join-Path $baseRoot "Config"
$secretRoot = Join-Path $baseRoot "Secrets"
$scriptRoot = Join-Path $baseRoot "Scripts"
$serviceRoot = Join-Path $baseRoot "Services"
$desktopRoot = Join-Path $baseRoot "Desktop"
$desktopToken = Join-Path $desktopRoot "companion.token"
$incomingRoot = Join-Path $baseRoot "Incoming"
$sourceUpdateRoot = Join-Path $baseRoot "SourceUpdates"
$sourceUpdateState = Join-Path $sourceUpdateRoot "state.json"
$sourceSnapshotsRoot = Join-Path $sourceUpdateRoot "Releases"
$lifecycleToken = Join-Path $secretRoot "source-lifecycle.token"
$installationStatePath = Join-Path $configRoot "installation.json"
$venvRoot = Join-Path $InstallRoot "venv"
$python = Join-Path $venvRoot "Scripts\python.exe"
$tokenFile = Join-Path $secretRoot "hosted-hub-bootstrap.token"
$releasePublishTokenFile = Join-Path $secretRoot "hosted-hub-release-publisher.token"
$nodeConfig = Join-Path $configRoot "node-windows.yaml"
$hubWrapper = Join-Path $serviceRoot "KnoaHostedHub\KnoaHostedHub.exe"
$nodeWrapper = Join-Path $serviceRoot "KnoaNode\KnoaNode.exe"

# Windows keeps loaded Python and WinSW files locked. Stop the selected
# processes before performing an in-place runtime update.
if ($installHub) { Stop-KnoaService "KnoaHostedHub" }
if ($installNode) { Stop-KnoaService "KnoaNode" }
if ($installNode) {
    Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | `
        Where-Object {
            $_.CommandLine -like "*Run-KnoaDesktopCompanion.ps1*" -or
            $_.CommandLine -like "*knoa_platform.desktop_companion*"
        } | `
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
}

# Stray Knoa runtime and wrapper processes from an interrupted earlier update
# keep the venv and service executables locked. Stop every process running
# from the install roots, except this script's own process tree (the source
# lifecycle broker invokes this installer as its child).
$protectedProcessIds = @()
$cursor = $PID
for ($hop = 0; $cursor -and ($hop -lt 16) -and ($protectedProcessIds -notcontains $cursor); $hop++) {
    $protectedProcessIds += $cursor
    $cursor = (Get-CimInstance Win32_Process -Filter "ProcessId=$cursor" -ErrorAction SilentlyContinue).ParentProcessId
}
Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | `
    Where-Object {
        $_.ExecutablePath -and
        ($protectedProcessIds -notcontains $_.ProcessId) -and (
            $_.ExecutablePath.StartsWith($InstallRoot, [StringComparison]::OrdinalIgnoreCase) -or
            $_.ExecutablePath.StartsWith($serviceRoot, [StringComparison]::OrdinalIgnoreCase)
        )
    } | `
    ForEach-Object {
        Write-Host "Stopping stray Knoa process $($_.ProcessId): $($_.ExecutablePath)"
        Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
    }

$legacyTaskNames = @()
if ($installHub) { $legacyTaskNames += "Knoa Hosted Hub" }
if ($installNode) { $legacyTaskNames += "Knoa Node" }
foreach ($taskName in $legacyTaskNames) {
    Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue
}
Protect-KnoaPath $baseRoot
if ($installNode) {
    $legacyNodeExists = $LegacyNodeRoot -and (Test-Path -LiteralPath $LegacyNodeRoot)
    $nodeRootsDiffer = $LegacyNodeRoot -and (
        [IO.Path]::GetFullPath($LegacyNodeRoot) -ne [IO.Path]::GetFullPath($NodeRoot)
    )
    if ($legacyNodeExists -and $nodeRootsDiffer) {
        $targetHasState = $false
        if (Test-Path -LiteralPath $NodeRoot) {
            $targetHasState = [bool](
                Get-ChildItem -Force -LiteralPath $NodeRoot | Select-Object -First 1
            )
        }
        if (-not $targetHasState) {
            New-Item -ItemType Directory -Force -Path $NodeRoot | Out-Null
            Get-ChildItem -Force -LiteralPath $LegacyNodeRoot |
                Copy-Item -Destination $NodeRoot -Recurse -Force
            Write-Host "Copied the legacy per-user Node identity to $NodeRoot. The source is retained as a rollback snapshot."
        }
    }
    Protect-KnoaPath $NodeRoot -Recursive
}
New-Item -ItemType Directory -Force -Path $configRoot, $secretRoot, $scriptRoot, $serviceRoot, $desktopRoot, $incomingRoot, $sourceUpdateRoot, $sourceSnapshotsRoot | Out-Null

if ($RecreateVenv -and (Test-Path -LiteralPath $venvRoot)) {
    Remove-Item -LiteralPath $venvRoot -Recurse -Force
}
$existingEnvironment = Test-Path -LiteralPath $python
if (-not (Test-Path -LiteralPath $python)) {
    & py "-$PythonVersion" -m venv $venvRoot
    if ($LASTEXITCODE -ne 0) { throw "Python $PythonVersion venv creation failed" }
}
$pythonProbe = @'
import struct
import sys
import sysconfig

print(
    f"{sys.version_info.major}.{sys.version_info.minor}:"
    f"{struct.calcsize('P') * 8}:"
    f"{int(bool(sysconfig.get_config_var('Py_GIL_DISABLED')))}"
)
'@
$pythonIdentityLines = @($pythonProbe | & $python -)
if ($LASTEXITCODE -ne 0) {
    throw "Could not inspect the Knoa Python environment"
}
$pythonIdentity = ($pythonIdentityLines -join "`n").Trim()
if ($pythonIdentity -ne "${PythonVersion}:64:0") {
    throw "Knoa requires standard CPython $PythonVersion x64; found $pythonIdentity. Use -RecreateVenv after installing it."
}
if ($WheelhousePath) {
    $wheelhouse = (Resolve-Path -LiteralPath $WheelhousePath).Path
    & $python -m pip install --no-index --find-links $wheelhouse --force-reinstall $resolvedPackage
} elseif ($sourceInstall -and $existingEnvironment) {
    # A source update can introduce new runtime dependencies. Let pip reconcile
    # them instead of replacing only Knoa and leaving a half-updated service.
    & $python -m pip install --upgrade --upgrade-strategy only-if-needed $resolvedPackage
} else {
    & $python -m pip install --force-reinstall $resolvedPackage
}
if ($LASTEXITCODE -ne 0) { throw "Knoa wheel installation failed" }
if ($installNode) {
    Install-KnoaP2PFirewallRule $python
    Install-KnoaMdnsFirewallRules
}

Copy-Item -Force (Join-Path $PSScriptRoot "Uninstall-Knoa.ps1") $scriptRoot
Copy-Item -Force (Join-Path $PSScriptRoot "Update-Knoa.ps1") $scriptRoot
Copy-Item -Force (Join-Path $PSScriptRoot "Update-Knoa.cmd") $scriptRoot
if ($sourceInstall) {
    Copy-Item -Force (Join-Path $PSScriptRoot "Run-KnoaHostLifecycle.ps1") $scriptRoot
}
if ($installHub) {
    Copy-Item -Force (Join-Path $PSScriptRoot "Run-KnoaHub.ps1") $scriptRoot
    Copy-Item -Force (Join-Path $PSScriptRoot "Publish-KnoaApp.ps1") $scriptRoot
    Copy-Item -Force (Join-Path $PSScriptRoot "Install-Cloudflared.ps1") $scriptRoot
    Copy-Item -Force (Join-Path $PSScriptRoot "Uninstall-Cloudflared.ps1") $scriptRoot
}
if ($installNode) {
    Copy-Item -Force (Join-Path $PSScriptRoot "Run-KnoaNode.ps1") $scriptRoot
    Copy-Item -Force (Join-Path $PSScriptRoot "Run-KnoaDesktopCompanion.ps1") $scriptRoot
    Copy-Item -Force (Join-Path $PSScriptRoot "Enroll-KnoaNode.ps1") $scriptRoot
    Copy-Item -Force (Join-Path $PSScriptRoot "Show-KnoaPairingQr.cmd") $scriptRoot
}
if ($sourceInstall -and -not (Test-Path -LiteralPath $lifecycleToken)) {
    Set-Content -LiteralPath $lifecycleToken -Value (New-RandomToken) -NoNewline -Encoding ASCII
}

if ($installHub) {
    if ($BootstrapTokenSource) {
        Copy-Item -Force (Resolve-Path -LiteralPath $BootstrapTokenSource).Path $tokenFile
    } elseif (-not (Test-Path -LiteralPath $tokenFile)) {
        Set-Content -LiteralPath $tokenFile -Value (New-RandomToken) -NoNewline -Encoding ASCII
    }
    if (((Get-Content -LiteralPath $tokenFile -Raw).Trim()).Length -lt 32) {
        throw "Hosted Hub bootstrap token must contain at least 32 characters"
    }
    if ($ReleasePublishTokenSource) {
        Copy-Item -Force (Resolve-Path -LiteralPath $ReleasePublishTokenSource).Path $releasePublishTokenFile
    } elseif (-not (Test-Path -LiteralPath $releasePublishTokenFile)) {
        Set-Content -LiteralPath $releasePublishTokenFile -Value (New-RandomToken) -NoNewline -Encoding ASCII
    }
    if (((Get-Content -LiteralPath $releasePublishTokenFile -Raw).Trim()).Length -lt 32) {
        throw "Hosted Hub release publisher token must contain at least 32 characters"
    }

    if ($HostedBackupPath) {
        $backup = (Resolve-Path -LiteralPath $HostedBackupPath).Path
        if ((Test-Path -LiteralPath $HubRoot) -and (Get-ChildItem -Force $HubRoot | Select-Object -First 1)) {
            throw "HubRoot must be empty before restoring a Hosted Hub backup"
        }
        & $python -m knoa_platform.hub.admin restore --backup $backup --root $HubRoot
        if ($LASTEXITCODE -ne 0) { throw "Hosted Hub restore failed" }
    } else {
        New-Item -ItemType Directory -Force -Path $HubRoot | Out-Null
    }
    Protect-KnoaPath $HubRoot -Recursive
}

if ($installNode) {
    if (-not (Test-Path -LiteralPath $desktopToken)) {
        Set-Content -LiteralPath $desktopToken -Value (New-RandomToken) -NoNewline -Encoding ASCII
    }
    & icacls.exe $desktopRoot /inheritance:r /grant:r "*S-1-5-18:(OI)(CI)F" "*S-1-5-32-544:(OI)(CI)F" "*S-1-5-32-545:(OI)(CI)RX" | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Could not configure the Desktop Companion ACL" }
    & icacls.exe $desktopToken /inheritance:r /grant:r "*S-1-5-18:F" "*S-1-5-32-544:F" "*S-1-5-32-545:R" | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Could not protect the Desktop Companion token" }
    $nodeRootYaml = Quote-Yaml $NodeRoot
    $workingDirectory = Join-Path $baseRoot "Workspace"
    Protect-KnoaPath $workingDirectory
    $workingDirectoryYaml = Quote-Yaml $workingDirectory
    $nodeConfigContent = @"
runtime_root: $nodeRootYaml
working_directory: $workingDirectoryYaml
service_host: '127.0.0.1'
service_port: $NodeCorePort
gateway_enabled: true
gateway_host: '127.0.0.1'
gateway_port: $NodeGatewayPort
gateway_lan_enabled: true
gateway_lan_host: '0.0.0.0'
gateway_lan_port: 9541
gateway_remote_enabled: false
gateway_public_url: ''
capability_mcp_host: '127.0.0.1'
capability_mcp_port: $NodeMcpPort
"@
    Write-Utf8NoBom $nodeConfig $nodeConfigContent
}

Protect-KnoaPath $baseRoot
if ($installNode) { Protect-KnoaPath $NodeRoot -Recursive }

$powerShell = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
$powerShellXml = Escape-Xml $powerShell
if ($installHub) {
    $hubRunner = Join-Path $scriptRoot "Run-KnoaHub.ps1"
    $hubLifecycleArguments = if ($sourceInstall) { " -LifecycleTokenFile `"$lifecycleToken`" -LifecycleIncomingRoot `"$incomingRoot`"" } else { "" }
    $hubArguments = "-NoProfile -ExecutionPolicy Bypass -File `"$hubRunner`" -PythonExecutable `"$python`" -HubRoot `"$HubRoot`" -BootstrapTokenFile `"$tokenFile`" -ReleasePublishTokenFile `"$releasePublishTokenFile`"$hubLifecycleArguments -HubId `"$HubId`" -Port $HubPort -PublicUrl `"$HubPublicUrl`""
    $hubXmlArguments = Escape-Xml $hubArguments
    $hubLogPath = Escape-Xml (Join-Path $baseRoot "Logs\Hub")
    $hubXml = @"
<service>
  <id>KnoaHostedHub</id>
  <name>Knoa Hosted Hub</name>
  <description>Knoa Hosted Hub and encrypted Relay</description>
  <executable>$powerShellXml</executable>
  <arguments>$hubXmlArguments</arguments>
  <workingdirectory>$(Escape-Xml $baseRoot)</workingdirectory>
  <startmode>Automatic</startmode>
  <delayedAutoStart>true</delayedAutoStart>
  <onfailure action="restart" delay="10 sec" />
  <stoptimeout>30 sec</stoptimeout>
  <stopparentprocessfirst>true</stopparentprocessfirst>
  <logpath>$hubLogPath</logpath>
  <log mode="roll-by-size">
    <sizeThreshold>10240</sizeThreshold>
    <keepFiles>8</keepFiles>
  </log>
</service>
"@
    Install-WinSWService "KnoaHostedHub" $hubXml $resolvedWinSW $serviceRoot
    Write-Host "Knoa Hosted Hub: http://127.0.0.1:$HubPort"
    Write-Host "Canonical Hub URL: $HubPublicUrl"
}

if ($installNode) {
    $nodeRunner = Join-Path $scriptRoot "Run-KnoaNode.ps1"
    $nodeLifecycleArguments = if ($sourceInstall) { " -LifecycleTokenFile `"$lifecycleToken`" -LifecycleIncomingRoot `"$incomingRoot`"" } else { "" }
    $nodeArguments = "-NoProfile -ExecutionPolicy Bypass -File `"$nodeRunner`" -PythonExecutable `"$python`" -NodeRoot `"$NodeRoot`" -ConfigPath `"$nodeConfig`" -DesktopCompanionTokenFile `"$desktopToken`"$nodeLifecycleArguments"
    $nodeXmlArguments = Escape-Xml $nodeArguments
    $nodeLogPath = Escape-Xml (Join-Path $baseRoot "Logs\Node")
    $nodeXml = @"
<service>
  <id>KnoaNode</id>
  <name>Knoa Node Runtime</name>
  <description>Knoa headless Agent, Task, LLM, MCP and Relay runtime</description>
  <executable>$powerShellXml</executable>
  <arguments>$nodeXmlArguments</arguments>
  <workingdirectory>$(Escape-Xml $workingDirectory)</workingdirectory>
  <startmode>Automatic</startmode>
  <delayedAutoStart>true</delayedAutoStart>
  <onfailure action="restart" delay="10 sec" />
  <stoptimeout>30 sec</stoptimeout>
  <stopparentprocessfirst>true</stopparentprocessfirst>
  <logpath>$nodeLogPath</logpath>
  <log mode="roll-by-size">
    <sizeThreshold>10240</sizeThreshold>
    <keepFiles>8</keepFiles>
  </log>
</service>
"@
    Install-WinSWService "KnoaNode" $nodeXml $resolvedWinSW $serviceRoot
    $companionRunner = Join-Path $scriptRoot "Run-KnoaDesktopCompanion.ps1"
    $companionArguments = "-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$companionRunner`" -PythonExecutable `"$python`" -TokenFile `"$desktopToken`""
    $companionCommand = "powershell.exe $companionArguments"
    & reg.exe add "HKLM\Software\Microsoft\Windows\CurrentVersion\Run" /v "KnoaDesktopCompanion" /t REG_SZ /d $companionCommand /f | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Could not register the Desktop Companion login launcher" }
    Start-Process -FilePath $powerShell -ArgumentList $companionArguments -WindowStyle Hidden | Out-Null
    Write-Host "Knoa Node Gateway: http://127.0.0.1:$NodeGatewayPort"
    Write-Host "Knoa Node service: KnoaNode (WinSW)"
    Write-Host "Knoa Desktop Companion: current Windows session"
    $enrollmentFile = Join-Path $NodeRoot "data\node-hub.json"
    if ((Test-Path -LiteralPath $enrollmentFile) -and -not $SkipPairingQr) {
        Write-Host "Scan this QR in the Knoa App to bind the Windows Node:"
        & $python -m knoa_platform --config $nodeConfig gateway pair --ttl 600
        if ($LASTEXITCODE -ne 0) { throw "Could not create the App pairing QR" }
    } else {
        Write-Host "Use Enroll-KnoaNode.ps1 after the Windows Node has an Account token and Workspace ID."
    }
}

$hubServiceInstalled = [bool](Get-Service -Name "KnoaHostedHub" -ErrorAction SilentlyContinue)
$nodeServiceInstalled = [bool](Get-Service -Name "KnoaNode" -ErrorAction SilentlyContinue)
$effectiveRole = if ($hubServiceInstalled -and $nodeServiceInstalled) {
    "all"
} elseif ($hubServiceInstalled) {
    "hub"
} elseif ($nodeServiceInstalled) {
    "node"
} else {
    throw "No Knoa Windows service is installed"
}
$installMode = if ($sourceInstall) { "source" } else { "wheel" }
$installationSourcePath = if ($sourceInstall) { $ChannelSourcePath } else { "" }
$installedCommit = ""
if ($sourceInstall) {
    $gitExecutable = (Get-Command git.exe -ErrorAction Stop).Source
    $installedCommit = (& $gitExecutable -C $resolvedPackage rev-parse --verify HEAD).Trim().ToLowerInvariant()
    if ($LASTEXITCODE -ne 0 -or $installedCommit -notmatch "^[0-9a-f]{40}$") {
        throw "Could not determine the installed Knoa source revision"
    }
}
$installationState = [ordered]@{
    schema_version = 1
    install_mode = $installMode
    role = $effectiveRole
    source_path = $installationSourcePath
    installed_commit = $installedCommit
    hub_public_url = $HubPublicUrl
    hub_id = $HubId
    hub_root = $HubRoot
    node_root = $NodeRoot
    install_root = $InstallRoot
    python_version = $PythonVersion
    hub_port = $HubPort
    node_core_port = $NodeCorePort
    node_gateway_port = $NodeGatewayPort
    node_mcp_port = $NodeMcpPort
    updated_at = [DateTimeOffset]::UtcNow.ToString("o")
}
Write-Utf8NoBom $installationStatePath ($installationState | ConvertTo-Json -Depth 3)
if ($sourceInstall) {
    $lifecycleRunner = Join-Path $scriptRoot "Run-KnoaHostLifecycle.ps1"
    $lifecycleArguments = "-NoProfile -NonInteractive -ExecutionPolicy Bypass -File `"$lifecycleRunner`" -PythonExecutable `"$python`" -SourceRoot `"$ChannelSourcePath`" -SourceStateFile `"$sourceUpdateState`" -SourceSnapshotsRoot `"$sourceSnapshotsRoot`" -InstallationStateFile `"$installationStatePath`" -TokenFile `"$lifecycleToken`""
    $lifecycleXmlArguments = Escape-Xml $lifecycleArguments
    $lifecycleLogPath = Escape-Xml (Join-Path $baseRoot "Logs\Lifecycle")
    $lifecycleProxyEntries = @()
    foreach ($proxyName in @("HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY")) {
        $proxyValue = [Environment]::GetEnvironmentVariable($proxyName)
        if ($proxyValue) {
            $lifecycleProxyEntries += "  <env name=`"$(Escape-Xml $proxyName)`" value=`"$(Escape-Xml $proxyValue)`" />"
        }
    }
    $lifecycleProxyXml = $lifecycleProxyEntries -join "`r`n"
    $lifecycleXml = @"
<service>
  <id>KnoaHostLifecycle</id>
  <name>Knoa Source Lifecycle</name>
  <description>Knoa cross-platform source update and service lifecycle broker</description>
  <executable>$powerShellXml</executable>
  <arguments>$lifecycleXmlArguments</arguments>
  <workingdirectory>$(Escape-Xml $baseRoot)</workingdirectory>
$lifecycleProxyXml
  <startmode>Automatic</startmode>
  <delayedAutoStart>true</delayedAutoStart>
  <onfailure action="restart" delay="10 sec" />
  <stoptimeout>30 sec</stoptimeout>
  <stopparentprocessfirst>true</stopparentprocessfirst>
  <logpath>$lifecycleLogPath</logpath>
  <log mode="roll-by-size"><sizeThreshold>10240</sizeThreshold><keepFiles>8</keepFiles></log>
</service>
"@
    if ($env:KNOA_SOURCE_UPDATE_ACTIVE -ne "1") {
        Install-WinSWService "KnoaHostLifecycle" $lifecycleXml $resolvedWinSW $serviceRoot
    }
}
Write-Host "Local management: open the Hub or Node Console System page"
Write-Host "Recovery updater: $scriptRoot\Update-Knoa.cmd"
