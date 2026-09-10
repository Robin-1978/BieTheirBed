$ErrorActionPreference = 'Stop'
$src = 'C:\knoa\.bge-cache\models--Qdrant--bge-small-zh-v1.5'
$dstRoot = Join-Path $env:SystemRoot 'system32\config\systemprofile\.cache\knoa\fastembed'
$dst = Join-Path $dstRoot 'models--Qdrant--bge-small-zh-v1.5'
New-Item -ItemType Directory -Force -Path $dstRoot | Out-Null
if (Test-Path $dst) { Remove-Item -Recurse -Force $dst }
Copy-Item -Recurse -Force $src $dst
Write-Host "Copied to $dst"
Get-ChildItem -Recurse $dst | Measure-Object -Property Length -Sum | ForEach-Object { Write-Host ("files={0} bytes={1}" -f $_.Count, $_.Sum) }
Restart-Service -Name 'KnoaNode' -Force
Write-Host 'KnoaNode restarted'
