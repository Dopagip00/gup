$ErrorActionPreference = 'Stop'
$env:PYTHONUTF8='1'
function Get-PlatformPython {
    $portable = Join-Path $PSScriptRoot 'runtime\python.exe'
    if (Test-Path -LiteralPath $portable) { return $portable }
    $bundled = Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
    if (Test-Path -LiteralPath $bundled) { return $bundled }
    $local = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
    if (Test-Path -LiteralPath $local) { return $local }
    foreach ($name in @('python.exe', 'python3.exe')) {
        $command = Get-Command $name -ErrorAction SilentlyContinue
        if ($command -and $command.Source -notlike '*WindowsApps*') {
            & $command.Source -c "import sys; assert sys.version_info >= (3,10)" 2>$null
            if ($LASTEXITCODE -eq 0) { return $command.Source }
        }
    }
    throw 'Python 3.10+ is required. Install Python, select Add to PATH, and run again. No third-party packages are required.'
}
