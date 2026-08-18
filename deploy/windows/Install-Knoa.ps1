[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$WheelPath,
    [Parameter(Mandatory = $true)][string]$WinSWExecutable,
    [string]$WheelhousePath = "",
    [string]$HubRoot = "$env:ProgramData\Knoa\HostedHub",
    [string]$NodeRoot = "",
    [string]$InstallRoot = "$env:ProgramData\Knoa\Runtime",
    [string]$HubPublicUrl = "https://knoa.tinydotdot.com",
    [string]$HubId = "hub_knoa_hosted",
    [string]$HostedBackupPath = "",
    [string]$BootstrapTokenSource = "",
    [string]$PythonVersion = "3.14",
    [ValidateSet("InteractiveTask", "HeadlessService")]
    [string]$NodeMode = "InteractiveTask",
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
    Copy-Item -Force $SourceExecutable $wrapper
    Write-Utf8NoBom $configuration $Xml
    & $wrapper install
    if ($LASTEXITCODE -ne 0) { throw "Could not install WinSW service $ServiceId" }
    & $wrapper start
    if ($LASTEXITCODE -ne 0) { throw "Could not start WinSW service $ServiceId" }
}

Assert-Administrator
$resolvedWinSW = (Resolve-Path -LiteralPath $WinSWExecutable).Path
if (-not $NodeRoot) {
    if ($NodeMode -eq "HeadlessService") {
        $NodeRoot = "$env:ProgramData\Knoa\Node"
    } else {
        $NodeRoot = "$env:LOCALAPPDATA\Knoa\Node"
    }
}
$duplicatePorts = @($HubPort, $NodeCorePort, $NodeGatewayPort, $NodeMcpPort) |
    Group-Object |
    Where-Object { $_.Count -gt 1 }
if ($duplicatePorts) {
    throw "Hub, Node Core, Node Gateway and Node MCP ports must be distinct"
}

$resolvedWheel = (Resolve-Path -LiteralPath $WheelPath).Path
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
}
Unregister-ScheduledTask -TaskName "Knoa Hosted Hub" -Confirm:$false -ErrorAction SilentlyContinue
Remove-WinSWService "KnoaHostedHub" $hubWrapper
Remove-WinSWService "KnoaNode" $nodeWrapper

Protect-KnoaPath $baseRoot
Protect-KnoaPath $NodeRoot
New-Item -ItemType Directory -Force -Path $configRoot, $secretRoot, $scriptRoot, $serviceRoot | Out-Null

if ($RecreateVenv -and (Test-Path -LiteralPath $venvRoot)) {
    Remove-Item -LiteralPath $venvRoot -Recurse -Force
}
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
    & $python -m pip install --no-index --find-links $wheelhouse --force-reinstall $resolvedWheel
} else {
    & $python -m pip install --force-reinstall $resolvedWheel
}
if ($LASTEXITCODE -ne 0) { throw "Knoa wheel installation failed" }

Copy-Item -Force (Join-Path $PSScriptRoot "Run-KnoaHub.ps1") $scriptRoot
Copy-Item -Force (Join-Path $PSScriptRoot "Run-KnoaNode.ps1") $scriptRoot
Copy-Item -Force (Join-Path $PSScriptRoot "Enroll-KnoaNode.ps1") $scriptRoot
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
if ($NodeMode -eq "HeadlessService") {
    $workingDirectory = Join-Path $baseRoot "Workspace"
    Protect-KnoaPath $workingDirectory
} else {
    $workingDirectory = $env:USERPROFILE
}
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

if ($NodeMode -eq "HeadlessService") {
    Stop-ScheduledTask -TaskName "Knoa Node" -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName "Knoa Node" -Confirm:$false -ErrorAction SilentlyContinue
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
    Write-Warning "HeadlessService runs in Session 0; desktop capture, clipboard, input, windows and notifications are unavailable."
} else {
    Remove-WinSWService "KnoaNode" $nodeWrapper
    $settings = New-ScheduledTaskSettingsSet `
        -RestartCount 999 `
        -RestartInterval (New-TimeSpan -Minutes 1) `
        -StartWhenAvailable `
        -ExecutionTimeLimit ([TimeSpan]::Zero)
    $currentUser = [Security.Principal.WindowsIdentity]::GetCurrent().Name
    $nodeTask = New-ScheduledTask `
        -Action (New-ScheduledTaskAction -Execute $powerShell -Argument $nodeArguments) `
        -Trigger (New-ScheduledTaskTrigger -AtLogOn -User $currentUser) `
        -Principal (New-ScheduledTaskPrincipal -UserId $currentUser -LogonType Interactive -RunLevel Limited) `
        -Settings $settings
    Register-ScheduledTask -TaskName "Knoa Node" -InputObject $nodeTask -Force | Out-Null
    Start-ScheduledTask -TaskName "Knoa Node"
}

Write-Host "Knoa Hosted Hub: http://127.0.0.1:$HubPort"
Write-Host "Knoa Node Gateway: http://127.0.0.1:$NodeGatewayPort"
Write-Host "Canonical Hub URL: $HubPublicUrl"
Write-Host "Node mode: $NodeMode"
Write-Host "Use Enroll-KnoaNode.ps1 after the Windows Node has an Account token and Workspace ID."
