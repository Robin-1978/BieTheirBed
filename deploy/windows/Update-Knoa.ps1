[CmdletBinding()]
param(
    [string]$InstallationStatePath = "$env:ProgramData\Knoa\Config\installation.json",
    [string]$SourcePath = "",
    [string]$Role = "",
    [string]$HubPublicUrl = ""
)

$ErrorActionPreference = "Stop"

function Test-Administrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = [Security.Principal.WindowsPrincipal]::new($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Restart-InstalledServices([string]$SelectedRole) {
    $serviceIds = @()
    if ($SelectedRole -in @("all", "hub")) { $serviceIds += "KnoaHostedHub" }
    if ($SelectedRole -in @("all", "node")) { $serviceIds += "KnoaNode" }
    foreach ($serviceId in $serviceIds) {
        $service = Get-Service -Name $serviceId -ErrorAction SilentlyContinue
        if ($service -and $service.Status -ne "Running") {
            Start-Service -Name $serviceId
            $service.WaitForStatus(
                [System.ServiceProcess.ServiceControllerStatus]::Running,
                [TimeSpan]::FromSeconds(30)
            )
        }
    }
}

function Get-InstalledRole {
    $hubInstalled = [bool](Get-Service -Name "KnoaHostedHub" -ErrorAction SilentlyContinue)
    $nodeInstalled = [bool](Get-Service -Name "KnoaNode" -ErrorAction SilentlyContinue)
    if ($hubInstalled -and $nodeInstalled) { return "all" }
    if ($hubInstalled) { return "hub" }
    if ($nodeInstalled) { return "node" }
    return ""
}

if (-not (Test-Administrator)) {
    $powerShell = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
    $arguments = "-NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`""
    $elevated = Start-Process -FilePath $powerShell -ArgumentList $arguments `
        -Verb RunAs -Wait -PassThru
    exit $elevated.ExitCode
}

$state = $null
if (Test-Path -LiteralPath $InstallationStatePath) {
    $state = Get-Content -LiteralPath $InstallationStatePath -Raw -Encoding UTF8 |
        ConvertFrom-Json
    if ([int]$state.schema_version -ne 1) {
        throw "Unsupported Knoa installation state"
    }
}

if (-not $SourcePath) {
    $SourcePath = if ($state -and $state.source_path) {
        [string]$state.source_path
    } else {
        "C:\knoa"
    }
}
if (-not $Role) {
    $Role = Get-InstalledRole
    if (-not $Role) {
        throw "No installed Knoa WinSW service was detected; install Hub or Node before using the updater"
    }
}
if ($Role -notin @("all", "hub", "node")) {
    throw "Knoa update role must be all, hub or node"
}
if (-not $HubPublicUrl) {
    $HubPublicUrl = if ($state -and $state.hub_public_url) {
        [string]$state.hub_public_url
    } else {
        "https://knoa.tinydotdot.com"
    }
}

$resolvedSource = (Resolve-Path -LiteralPath $SourcePath).Path
$git = (Get-Command git.exe -ErrorAction Stop).Source
$trackedChanges = @(& $git -C $resolvedSource status --porcelain --untracked-files=no)
if ($LASTEXITCODE -ne 0) { throw "Could not inspect the Knoa source checkout" }
if ($trackedChanges.Count -gt 0) {
    throw "Knoa source checkout contains local tracked changes; update was not started"
}

Write-Host "Updating Knoa source in $resolvedSource"
& $git -C $resolvedSource pull --ff-only
if ($LASTEXITCODE -ne 0) { throw "Knoa git pull --ff-only failed" }

$installer = Join-Path $resolvedSource "deploy\windows\Install-Knoa.ps1"
if (-not (Test-Path -LiteralPath $installer)) {
    throw "Updated Knoa Windows installer is missing: $installer"
}

$installArguments = @{
    Role = $Role
    SourcePath = $resolvedSource
    HubPublicUrl = $HubPublicUrl
    SkipPairingQr = $true
}
if ($state) {
    if ($state.hub_root) { $installArguments["HubRoot"] = [string]$state.hub_root }
    if ($state.node_root) { $installArguments["NodeRoot"] = [string]$state.node_root }
    if ($state.install_root) {
        $installArguments["InstallRoot"] = [string]$state.install_root
    }
    if ($state.hub_id) { $installArguments["HubId"] = [string]$state.hub_id }
    if ($state.python_version) {
        $installArguments["PythonVersion"] = [string]$state.python_version
    }
    if ($state.hub_port) {
        $installArguments["HubPort"] = [int]$state.hub_port
    }
    if ($state.node_core_port) {
        $installArguments["NodeCorePort"] = [int]$state.node_core_port
    }
    if ($state.node_gateway_port) {
        $installArguments["NodeGatewayPort"] = [int]$state.node_gateway_port
    }
    if ($state.node_mcp_port) {
        $installArguments["NodeMcpPort"] = [int]$state.node_mcp_port
    }
}

try {
    & $installer @installArguments
} catch {
    Write-Warning "Knoa update failed; attempting to restore existing services"
    Restart-InstalledServices $Role
    throw
}

Restart-InstalledServices $Role
$commit = (& $git -C $resolvedSource rev-parse --short HEAD).Trim()
if ($LASTEXITCODE -ne 0) { throw "Could not read the installed Knoa revision" }
Write-Host "Knoa update completed successfully: $commit"
if ($Role -in @("all", "hub")) {
    Write-Host "KnoaHostedHub: $((Get-Service KnoaHostedHub).Status)"
}
if ($Role -in @("all", "node")) {
    Write-Host "KnoaNode: $((Get-Service KnoaNode).Status)"
}
