[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [switch]$PurgeData,
    [string]$BaseRoot = "$env:ProgramData\Knoa",
    [string]$NodeRoot = "$env:LOCALAPPDATA\Knoa\Node"
)

$ErrorActionPreference = "Stop"
foreach ($task in @("Knoa Hosted Hub", "Knoa Node")) {
    Stop-ScheduledTask -TaskName $task -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $task -Confirm:$false -ErrorAction SilentlyContinue
}
foreach ($serviceId in @("KnoaHostedHub", "KnoaNode")) {
    $wrapper = Join-Path $BaseRoot "Services\$serviceId\$serviceId.exe"
    if ((Test-Path -LiteralPath $wrapper) -and
        (Get-Service -Name $serviceId -ErrorAction SilentlyContinue)) {
        & $wrapper stop | Out-Null
        & $wrapper uninstall | Out-Null
    }
}
if ($PurgeData -and $PSCmdlet.ShouldProcess("$BaseRoot and $NodeRoot", "Delete Knoa data")) {
    Remove-Item -LiteralPath $BaseRoot -Recurse -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $NodeRoot -Recurse -Force -ErrorAction SilentlyContinue
}
Write-Host "Knoa services and scheduled tasks removed. Data was preserved unless -PurgeData was supplied."
