[CmdletBinding()]
param(
    [string]$WheelPath = "",
    [string]$SourcePath = "",
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
    [string]$PythonVersion = "3.14",
    [switch]$RecreateVenv,
    [int]$HubPort = 9529,
    [int]$NodeCorePort = 9527,
    [int]$NodeGatewayPort = 9531,
    [int]$NodeMcpPort = 9530
)

$ErrorActionPreference = "Stop"

function Assert-Administrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = [Security.Principal.WindowsPrincipal]::new($identity)
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw "Run Install-Knoa.ps1 from an elevated PowerShell window"
    }
}

function Protect-KnoaPath([string]$Path) {
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

function Install-WinSWService(
    [string]$ServiceId,
    [string]$Xml,
    [string]$SourceExecutable,
    [string]$ServicesRoot
) {
    $serviceDirectory = Join-Path $ServicesRoot $ServiceId
    $wrapper = Join-Path $serviceDirectory "$ServiceId.exe"
    $configuration = Join-Path $serviceDirectory "$ServiceId.xml"
    New-Item -ItemType Directory -Force -Path $serviceDirectory | Out-Null
    Remove-WinSWService $ServiceId $wrapper
    if ([IO.Path]::GetFullPath($SourceExecutable) -ne [IO.Path]::GetFullPath($wrapper)) {
        Copy-Item -Force $SourceExecutable $wrapper
    }
    Write-Utf8NoBom $configuration $Xml
    & $wrapper install
    if ($LASTEXITCODE -ne 0) { throw "Could not install WinSW service $ServiceId" }
    & $wrapper start
    if ($LASTEXITCODE -ne 0) { throw "Could not start WinSW service $ServiceId" }
}

Assert-Administrator
if (-not $WinSWExecutable) {
    $existingWrapper = "$env:ProgramData\Knoa\Services\KnoaHostedHub\KnoaHostedHub.exe"
    if (Test-Path -LiteralPath $existingWrapper) {
        $WinSWExecutable = $existingWrapper
    } else {
        throw "WinSWExecutable is required for the first installation"
    }
}
$resolvedWinSW = (Resolve-Path -LiteralPath $WinSWExecutable).Path
$duplicatePorts = @($HubPort, $NodeCorePort, $NodeGatewayPort, $NodeMcpPort) |
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
$baseRoot = Split-Path -Parent $InstallRoot
$configRoot = Join-Path $baseRoot "Config"
$secretRoot = Join-Path $baseRoot "Secrets"
$scriptRoot = Join-Path $baseRoot "Scripts"
$serviceRoot = Join-Path $baseRoot "Services"
$venvRoot = Join-Path $InstallRoot "venv"
$python = Join-Path $venvRoot "Scripts\python.exe"
$tokenFile = Join-Path $secretRoot "hosted-hub-bootstrap.token"
$nodeConfig = Join-Path $configRoot "node-windows.yaml"
$hubWrapper = Join-Path $serviceRoot "KnoaHostedHub\KnoaHostedHub.exe"
$nodeWrapper = Join-Path $serviceRoot "KnoaNode\KnoaNode.exe"

foreach ($taskName in @("Knoa Hosted Hub", "Knoa Node")) {
    Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue
}
Remove-WinSWService "KnoaHostedHub" $hubWrapper
Remove-WinSWService "KnoaNode" $nodeWrapper

Protect-KnoaPath $baseRoot
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
Protect-KnoaPath $NodeRoot
New-Item -ItemType Directory -Force -Path $configRoot, $secretRoot, $scriptRoot, $serviceRoot | Out-Null

if ($RecreateVenv -and (Test-Path -LiteralPath $venvRoot)) {
    Remove-Item -LiteralPath $venvRoot -Recurse -Force
}
$existingEnvironment = Test-Path -LiteralPath $python
if (-not (Test-Path -LiteralPath $python)) {
    & py "-$PythonVersion" -m venv $venvRoot
    if ($LASTEXITCODE -ne 0) { throw "Python $PythonVersion venv creation failed" }
}
$pythonIdentity = (& $python -c "import struct,sys,sysconfig; print(f'{sys.version_info.major}.{sys.version_info.minor}:{struct.calcsize(`"P`") * 8}:{int(bool(sysconfig.get_config_var(`"Py_GIL_DISABLED`")))}')").Trim()
if ($pythonIdentity -ne "${PythonVersion}:64:0") {
    throw "Knoa requires standard CPython $PythonVersion x64; found $pythonIdentity. Use -RecreateVenv after installing it."
}
if ($WheelhousePath) {
    $wheelhouse = (Resolve-Path -LiteralPath $WheelhousePath).Path
    & $python -m pip install --no-index --find-links $wheelhouse --force-reinstall $resolvedPackage
} elseif ($sourceInstall -and $existingEnvironment) {
    & $python -m pip install --no-deps --force-reinstall $resolvedPackage
} else {
    & $python -m pip install --force-reinstall $resolvedPackage
}
if ($LASTEXITCODE -ne 0) { throw "Knoa wheel installation failed" }

Copy-Item -Force (Join-Path $PSScriptRoot "Run-KnoaHub.ps1") $scriptRoot
Copy-Item -Force (Join-Path $PSScriptRoot "Run-KnoaNode.ps1") $scriptRoot
Copy-Item -Force (Join-Path $PSScriptRoot "Enroll-KnoaNode.ps1") $scriptRoot
Copy-Item -Force (Join-Path $PSScriptRoot "Show-KnoaPairingQr.cmd") $scriptRoot
Copy-Item -Force (Join-Path $PSScriptRoot "Install-Cloudflared.ps1") $scriptRoot
Copy-Item -Force (Join-Path $PSScriptRoot "Uninstall-Cloudflared.ps1") $scriptRoot
Copy-Item -Force (Join-Path $PSScriptRoot "Uninstall-Knoa.ps1") $scriptRoot

if ($BootstrapTokenSource) {
    Copy-Item -Force (Resolve-Path -LiteralPath $BootstrapTokenSource).Path $tokenFile
} elseif (-not (Test-Path -LiteralPath $tokenFile)) {
    Set-Content -LiteralPath $tokenFile -Value (New-RandomToken) -NoNewline -Encoding ASCII
}
if (((Get-Content -LiteralPath $tokenFile -Raw).Trim()).Length -lt 32) {
    throw "Hosted Hub bootstrap token must contain at least 32 characters"
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
gateway_remote_enabled: false
gateway_public_url: ''
capability_mcp_host: '127.0.0.1'
capability_mcp_port: $NodeMcpPort
"@
Write-Utf8NoBom $nodeConfig $nodeConfigContent

Protect-KnoaPath $baseRoot
Protect-KnoaPath $NodeRoot

$powerShell = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
$hubRunner = Join-Path $scriptRoot "Run-KnoaHub.ps1"
$nodeRunner = Join-Path $scriptRoot "Run-KnoaNode.ps1"
$hubArguments = "-NoProfile -ExecutionPolicy Bypass -File `"$hubRunner`" -PythonExecutable `"$python`" -HubRoot `"$HubRoot`" -BootstrapTokenFile `"$tokenFile`" -HubId `"$HubId`" -Port $HubPort"
$nodeArguments = "-NoProfile -ExecutionPolicy Bypass -File `"$nodeRunner`" -PythonExecutable `"$python`" -NodeRoot `"$NodeRoot`" -ConfigPath `"$nodeConfig`""

$hubXmlArguments = Escape-Xml $hubArguments
$powerShellXml = Escape-Xml $powerShell
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
  <logpath>$hubLogPath</logpath>
  <log mode="roll-by-size">
    <sizeThreshold>10240</sizeThreshold>
    <keepFiles>8</keepFiles>
  </log>
</service>
"@
Install-WinSWService "KnoaHostedHub" $hubXml $resolvedWinSW $serviceRoot

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
  <logpath>$nodeLogPath</logpath>
  <log mode="roll-by-size">
    <sizeThreshold>10240</sizeThreshold>
    <keepFiles>8</keepFiles>
  </log>
</service>
"@
Install-WinSWService "KnoaNode" $nodeXml $resolvedWinSW $serviceRoot

Write-Host "Knoa Hosted Hub: http://127.0.0.1:$HubPort"
Write-Host "Knoa Node Gateway: http://127.0.0.1:$NodeGatewayPort"
Write-Host "Canonical Hub URL: $HubPublicUrl"
Write-Host "Knoa Node service: KnoaNode (WinSW)"
$enrollmentFile = Join-Path $NodeRoot "data\node-hub.json"
if (Test-Path -LiteralPath $enrollmentFile) {
    Write-Host "Scan this QR in the Knoa App to bind the Windows Node:"
    & $python -m knoa_platform --config $nodeConfig gateway pair --ttl 600
    if ($LASTEXITCODE -ne 0) { throw "Could not create the App pairing QR" }
} else {
    Write-Host "Use Enroll-KnoaNode.ps1 after the Windows Node has an Account token and Workspace ID."
}
