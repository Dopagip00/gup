param([switch]$Background)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'runtime.ps1')
$python = Get-PlatformPython
$isRunning = $false
try {
    $health = Invoke-RestMethod -Uri 'http://127.0.0.1:8765/api/health' -TimeoutSec 3
    $isRunning = $health.ok -eq $true
} catch { }
if ($isRunning -and $health.project_path -and $health.project_path -ne $PSScriptRoot) { throw '端口8765由另一个项目副本占用，请先停止旧副本。' }
if (-not $isRunning) {
    $pythonw = Join-Path (Split-Path -Parent $python) 'pythonw.exe'
    if (-not (Test-Path -LiteralPath $pythonw)) { $pythonw = $python }
    $app = Join-Path $PSScriptRoot 'app.py'
    Start-Process -FilePath $pythonw -ArgumentList @('"' + $app + '"', 'serve') -WorkingDirectory $PSScriptRoot -WindowStyle Hidden
    for ($attempt=0; $attempt -lt 30; $attempt++) {
        Start-Sleep -Milliseconds 300
        try {
            $health = Invoke-RestMethod -Uri 'http://127.0.0.1:8765/api/health' -TimeoutSec 2
            if ($health.ok) { $isRunning=$true; break }
        } catch { }
    }
    if (-not $isRunning) { throw 'Platform did not start. See data/app.log or run python app.py in a terminal.' }
}
if (-not $Background) { Start-Process 'http://127.0.0.1:8765' }
Write-Output 'Platform running: http://127.0.0.1:8765'
