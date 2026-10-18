param([string]$Output, [switch]$CodeOnly)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'runtime.ps1')
$platformPython = Get-PlatformPython
$archiveKind = if ($CodeOnly) { 'export-update' } else { 'backup' }
if (-not $Output) {
    $archivePrefix = if ($CodeOnly) { 'code-update' } else { 'full-backup' }
    $Output = Join-Path $PSScriptRoot ('backups\' + $archivePrefix + '-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '.zip')
}
$Output = [System.IO.Path]::GetFullPath($Output)
& $platformPython (Join-Path $PSScriptRoot 'portability.py') $archiveKind --output $Output
if ($LASTEXITCODE -ne 0) { throw 'Archive was not created. Read the error above.' }
Write-Output ('Archive ready: ' + $Output)
