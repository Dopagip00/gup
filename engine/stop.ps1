param([int]$Port=8765)
$ErrorActionPreference='Stop'
$base='http://127.0.0.1:'+ $Port
try { $health=Invoke-RestMethod -Uri ($base+'/api/health') -TimeoutSec 3 } catch { Write-Output '本地平台未运行。'; exit 0 }
if (-not $health.ok -or $health.project_path -ne $PSScriptRoot) { throw '该端口不是本目录的平台，拒绝停止。请先确认对应程序。' }
$config=Invoke-RestMethod -Uri ($base+'/api/config') -TimeoutSec 3
Invoke-RestMethod -Method Post -Uri ($base+'/api/shutdown') -Headers @{'X-CSRF-Token'=$config.csrf_token} -ContentType 'application/json' -Body '{}' -TimeoutSec 5 | Out-Null
Write-Output '本地平台已请求停止。'
