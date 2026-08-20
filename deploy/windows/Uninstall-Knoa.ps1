[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [ValidateSet("all", "hub", "node")]
    [string]$Role = "all",
    [switch]$PurgeData,
    [string]$BaseRoot = "$env:ProgramData\Knoa",
    [string]$HubRoot = "$env:ProgramData\Knoa\HostedHub",
    [string]$NodeRoot = "$env:ProgramData\Knoa\Node"
)

$ErrorActionPreference = "Stop"
$removeHub = $Role -in @("all", "hub")
$removeNode = $Role -in @("all", "node")
$tasks = @()
$services = @()
if ($removeHub) {
    $tasks += "Knoa Hosted Hub"
    $services += "KnoaHostedHub"
}
if ($removeNode) {
    $tasks += "Knoa Node"
    $services += "KnoaNode"
    & reg.exe delete "HKLM\Software\Microsoft\Windows\CurrentVersion\Run" /v "KnoaDesktopCompanion" /f 2>$null | Out-Null
    Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | `
        Where-Object {
            $_.CommandLine -like "*Run-KnoaDesktopCompanion.ps1*" -or
            $_.CommandLine -like "*knoa_platform.desktop_companion*"
        } | `
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
}
foreach ($task in $tasks) {
    Stop-ScheduledTask -TaskName $task -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $task -Confirm:$false -ErrorAction SilentlyContinue
}
foreach ($serviceId in $services) {
    $wrapper = Join-Path $BaseRoot "Services\$serviceId\$serviceId.exe"
    if ((Test-Path -LiteralPath $wrapper) -and
        (Get-Service -Name $serviceId -ErrorAction SilentlyContinue)) {
        & $wrapper stop | Out-Null
        & $wrapper uninstall | Out-Null
    }
}
if ($PurgeData) {
    if ($Role -eq "all" -and $PSCmdlet.ShouldProcess($BaseRoot, "Delete all Knoa data")) {
        Remove-Item -LiteralPath $BaseRoot -Recurse -Force -ErrorAction SilentlyContinue
    } elseif ($Role -eq "hub" -and $PSCmdlet.ShouldProcess($HubRoot, "Delete Hosted Hub data")) {
        Remove-Item -LiteralPath $HubRoot -Recurse -Force -ErrorAction SilentlyContinue
    } elseif ($Role -eq "node" -and $PSCmdlet.ShouldProcess($NodeRoot, "Delete Node data")) {
        Remove-Item -LiteralPath $NodeRoot -Recurse -Force -ErrorAction SilentlyContinue
    }
}
Write-Host "Knoa role '$Role' WinSW services removed. Matching legacy scheduled tasks were deleted. Data was preserved unless -PurgeData was supplied."
