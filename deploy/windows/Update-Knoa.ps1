[CmdletBinding()]
param(
    [string]$InstallationStatePath = "$env:ProgramData\Knoa\Config\installation.json",
    [string]$SourcePath = "",
    [string]$Role = "",
    [string]$HubPublicUrl = ""
)

$ErrorActionPreference = "Stop"

function Get-ListeningPids([int[]]$Ports) {
    $owners = @{}
    foreach ($port in $Ports) { $owners[$port] = @() }
    foreach ($connection in (Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue)) {
        if ($owners.ContainsKey([int]$connection.LocalPort)) {
            $owners[[int]$connection.LocalPort] += [int]$connection.OwningProcess
        }
    }
    return $owners
}

function Stop-KnoaPortOwners([int[]]$Ports) {
    $owners = Get-ListeningPids $Ports
    foreach ($entry in $owners.GetEnumerator()) {
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
    throw "Knoa ports were not released before restart: $detail"
}

function Test-LifecycleService {
    $service = Get-Service -Name "KnoaHostLifecycle" -ErrorAction SilentlyContinue
    if (-not $service) { return }
    $service.Refresh()
    if ($service.Status -ne "Running") { throw "KnoaHostLifecycle service is not running ($($service.Status))" }
    $deadline = [DateTime]::UtcNow.AddSeconds(20)
    do {
        if (Get-NetTCPConnection -State Listen -LocalPort 9533 -ErrorAction SilentlyContinue) { return }
        Start-Sleep -Milliseconds 500
    } while ([DateTime]::UtcNow -lt $deadline)
    throw "KnoaHostLifecycle is running but port 9533 is not listening"
}

function Test-Administrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = [Security.Principal.WindowsPrincipal]::new($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Restart-InstalledServices([string]$SelectedRole) {
    $serviceIds = @()
    if ($SelectedRole -in @("all", "hub")) { $serviceIds += "KnoaHostedHub" }
    if ($SelectedRole -in @("all", "node")) { $serviceIds += "KnoaNode" }
    $ports = @()
    if ($SelectedRole -in @("all", "hub")) { $ports += 9529, 9532 }
    if ($SelectedRole -in @("all", "node")) { $ports += 9527, 9530, 9531, 9541 }
    foreach ($serviceId in $serviceIds) {
        $service = Get-Service -Name $serviceId -ErrorAction SilentlyContinue
        if ($service -and $service.Status -ne "Stopped") {
            Stop-Service -Name $serviceId -Force -ErrorAction Stop
            $service.WaitForStatus([System.ServiceProcess.ServiceControllerStatus]::Stopped, [TimeSpan]::FromSeconds(30))
        }
    }
    Wait-KnoaPortsReleased $ports
    foreach ($serviceId in $serviceIds) {
        $service = Get-Service -Name $serviceId -ErrorAction SilentlyContinue
        if ($service) {
            Start-Service -Name $serviceId
            $service.WaitForStatus(
                [System.ServiceProcess.ServiceControllerStatus]::Running,
                [TimeSpan]::FromSeconds(30)
            )
        }
        $service = Get-Service -Name $serviceId -ErrorAction Stop
        $service.Refresh()
        if ($service.Status -ne "Running") {
            throw "Knoa service did not remain running: $serviceId ($($service.Status))"
        }
        Start-Sleep -Seconds 2
        $service.Refresh()
        if ($service.Status -ne "Running") {
            throw "Knoa service exited during startup: $serviceId ($($service.Status))"
        }
    }
    Test-LifecycleService
}

function Test-NodeGatewayHealth([int]$Port) {
    $uri = "http://127.0.0.1:$Port/health"
    for ($attempt = 0; $attempt -lt 20; $attempt++) {
        try {
            $response = Invoke-WebRequest -UseBasicParsing -Uri $uri -TimeoutSec 2
            if ($response.StatusCode -eq 200) { return }
        } catch {
            Start-Sleep -Milliseconds 500
        }
    }
    throw "Knoa Node Gateway health check failed: $uri"
}

function Get-InstalledRole {
    $hubInstalled = [bool](Get-Service -Name "KnoaHostedHub" -ErrorAction SilentlyContinue)
    $nodeInstalled = [bool](Get-Service -Name "KnoaNode" -ErrorAction SilentlyContinue)
    if ($hubInstalled -and $nodeInstalled) { return "all" }
    if ($hubInstalled) { return "hub" }
    if ($nodeInstalled) { return "node" }
    return ""
}

function Get-DeclaredKnoaVersion([string]$SourceRoot) {
    $versionFile = Join-Path $SourceRoot "src\knoa_platform\__init__.py"
    if (-not (Test-Path -LiteralPath $versionFile)) {
        throw "Knoa source version file is missing: $versionFile"
    }
    $match = Select-String -LiteralPath $versionFile -Pattern '__version__\s*=\s*["'']([^"'']+)["'']' |
        Select-Object -First 1
    if (-not $match -or -not $match.Matches.Count) {
        throw "Could not read Knoa source version from $versionFile"
    }
    return $match.Matches[0].Groups[1].Value
}

function Get-InstalledKnoaVersion([string]$PythonPath) {
    if (-not (Test-Path -LiteralPath $PythonPath)) {
        throw "Knoa runtime Python is missing: $PythonPath"
    }
    $output = @(& $PythonPath -c "import knoa_platform; print(knoa_platform.__version__)")
    if ($LASTEXITCODE -ne 0 -or $output.Count -eq 0) {
        throw "Could not read the installed Knoa runtime version"
    }
    return ([string]($output | Select-Object -Last 1)).Trim()
}

function Assert-InstalledRuntimeMatchesSource([string]$SourceRoot, $InstallState) {
    $installRoot = if ($InstallState -and $InstallState.install_root) {
        [string]$InstallState.install_root
    } else {
        "$env:ProgramData\Knoa\Runtime"
    }
    $pythonPath = Join-Path $installRoot "venv\Scripts\python.exe"
    $sourceVersion = Get-DeclaredKnoaVersion $SourceRoot
    $installedVersion = Get-InstalledKnoaVersion $pythonPath
    if ($sourceVersion -ne $installedVersion) {
        throw "Knoa runtime version mismatch: source=$sourceVersion installed=$installedVersion"
    }
    Write-Host "Knoa runtime version verified: $installedVersion"
}

if (-not (Test-Administrator)) {
    $powerShell = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
    $argumentList = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $PSCommandPath)
    foreach ($parameterName in @("InstallationStatePath", "SourcePath", "Role", "HubPublicUrl")) {
        if ($PSBoundParameters.ContainsKey($parameterName)) {
            $argumentList += "-$parameterName"
            $argumentList += [string]$PSBoundParameters[$parameterName]
        }
    }
    $elevated = Start-Process -FilePath $powerShell -ArgumentList $argumentList `
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

$previousCommit = (& $git -C $resolvedSource rev-parse --verify HEAD).Trim()
if ($LASTEXITCODE -ne 0 -or $previousCommit -notmatch "^[0-9a-f]{40}$") {
    throw "Could not determine the current Knoa source revision"
}
$backupRoot = Join-Path $env:ProgramData ("Knoa\Backups\Updates\" + [DateTime]::UtcNow.ToString("yyyyMMdd-HHmmss"))
New-Item -ItemType Directory -Force -Path $backupRoot | Out-Null
Set-Content -LiteralPath (Join-Path $backupRoot "source-commit.txt") -Value $previousCommit -Encoding UTF8
if (Test-Path -LiteralPath $InstallationStatePath) {
    Copy-Item -LiteralPath $InstallationStatePath -Destination (Join-Path $backupRoot "installation.json") -Force
}
Write-Host "Update backup created: $backupRoot"

Write-Host "Updating Knoa source in $resolvedSource"
$sourceBranch = (& $git -C $resolvedSource symbolic-ref --short HEAD).Trim()
if ($LASTEXITCODE -ne 0 -or -not $sourceBranch) {
    throw "Knoa source checkout is not on a named branch"
}
& $git -C $resolvedSource fetch --prune origin $sourceBranch
if ($LASTEXITCODE -ne 0) { throw "Knoa git fetch from origin failed" }
& $git -C $resolvedSource merge --ff-only "origin/$sourceBranch"
if ($LASTEXITCODE -ne 0) {
    throw "Knoa source is not fast-forwardable from origin/$sourceBranch; check the remote and branch"
}

$sourceManifest = Join-Path $resolvedSource "release\versions.json"
$sourceVersionFile = Join-Path $resolvedSource "src\knoa_platform\__init__.py"
if (-not (Test-Path -LiteralPath $sourceManifest) -or -not (Test-Path -LiteralPath $sourceVersionFile)) {
    throw "Updated Knoa source is missing release version metadata; refusing to install stale code"
}
$sourceManifestDocument = Get-Content -LiteralPath $sourceManifest -Raw -Encoding UTF8 | ConvertFrom-Json
$sourceVersion = Get-DeclaredKnoaVersion $resolvedSource
if ([string]$sourceManifestDocument.platform_version -ne $sourceVersion) {
    throw "Knoa source release metadata is inconsistent: source=$sourceVersion manifest=$($sourceManifestDocument.platform_version)"
}
Write-Host "Knoa source updated to $sourceBranch / $((& $git -C $resolvedSource rev-parse --short HEAD).Trim()) version $sourceVersion"

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

function Restore-PreviousRevision {
    Write-Warning "Restoring Knoa source revision $previousCommit"
    & $git -C $resolvedSource reset --hard $previousCommit
    if ($LASTEXITCODE -ne 0) { throw "Could not restore Knoa source revision $previousCommit" }
    & $installer @installArguments
    if ($LASTEXITCODE -ne 0) { throw "Could not reinstall the previous Knoa revision" }
    Restart-InstalledServices $Role
    if ($Role -in @("all", "node")) {
        $nodePort = if ($state -and $state.node_gateway_port) { [int]$state.node_gateway_port } else { 9531 }
        Test-NodeGatewayHealth $nodePort
    }
}

try {
    & $installer @installArguments
    if ($LASTEXITCODE -ne 0) { throw "Knoa installer returned exit code $LASTEXITCODE" }
    $updatedState = $null
    if (Test-Path -LiteralPath $InstallationStatePath) {
        $updatedState = Get-Content -LiteralPath $InstallationStatePath -Raw -Encoding UTF8 |
            ConvertFrom-Json
    }
    Assert-InstalledRuntimeMatchesSource $resolvedSource $updatedState
    Restart-InstalledServices $Role
    if ($Role -in @("all", "node")) {
        $nodePort = if ($state -and $state.node_gateway_port) { [int]$state.node_gateway_port } else { 9531 }
        Test-NodeGatewayHealth $nodePort
    }
} catch {
    $updateError = $_
    Write-Warning "Knoa update failed; attempting automatic rollback"
    try {
        Restore-PreviousRevision
        Write-Warning "Knoa rollback completed; previous services are healthy"
    } catch {
        Write-Error "Knoa rollback failed: $($_.Exception.Message)"
    }
    throw $updateError
}
$commit = (& $git -C $resolvedSource rev-parse --short HEAD).Trim()
if ($LASTEXITCODE -ne 0) { throw "Could not read the installed Knoa revision" }
Write-Host "Knoa update completed successfully: $commit"
if ($Role -in @("all", "hub")) {
    Write-Host "KnoaHostedHub: $((Get-Service KnoaHostedHub).Status)"
}
if ($Role -in @("all", "node")) {
    Write-Host "KnoaNode: $((Get-Service KnoaNode).Status)"
}
