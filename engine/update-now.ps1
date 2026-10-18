$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'runtime.ps1')
$python = Get-PlatformPython
& $python (Join-Path $PSScriptRoot 'app.py') update --force
exit $LASTEXITCODE
