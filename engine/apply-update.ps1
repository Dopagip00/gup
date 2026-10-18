param([Parameter(Mandatory=$true)][string]$Package, [int]$Port=8765)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'runtime.ps1')
$platformPython = Get-PlatformPython
$resolvedPackage = (Resolve-Path -LiteralPath $Package -ErrorAction Stop).Path
& $platformPython (Join-Path $PSScriptRoot 'portability.py') apply-update --package $resolvedPackage --port $Port
if ($LASTEXITCODE -ne 0) { throw 'Update was not applied. Read the error above. No unknown process is terminated.' }
Write-Output 'Code updated. Existing data is preserved. Run start.cmd when ready.'
