<#
    Builds dist\WorkspaceChecker\WorkspaceChecker.exe and a distributable zip.

    A one-folder build is intentional: --onefile self-extracts to %TEMP% on every
    launch, which agency endpoint protection routinely quarantines.
#>
[CmdletBinding()]
param(
    [switch]$SkipTests,
    [switch]$OneFile
)

$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot

$python = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path $python)) { throw "Virtual environment not found. Run: py -3.12 -m venv .venv" }

Write-Host 'Installing build dependencies...' -ForegroundColor Cyan
& $python -m pip install --quiet --upgrade pyinstaller openpyxl pytest

if (-not $SkipTests) {
    Write-Host 'Running tests...' -ForegroundColor Cyan
    & $python -m pytest -q
    if ($LASTEXITCODE -ne 0) { throw 'Tests failed; build aborted.' }
}

Write-Host 'Building executable...' -ForegroundColor Cyan

# A running instance holds dist\ open and PyInstaller only reports "Access is denied".
$running = Get-Process -ErrorAction SilentlyContinue | Where-Object { $_.ProcessName -eq 'WorkspaceChecker' }
if ($running) {
    throw "WorkspaceChecker is still running (PID $($running.Id -join ', ')). Close it, then rebuild."
}

Remove-Item build, dist -Recurse -Force -ErrorAction SilentlyContinue

$args = @(
    '-m', 'PyInstaller',
    '--noconfirm',
    '--clean',
    '--name', 'WorkspaceChecker',
    '--console',
    '--collect-submodules', 'workspace_checker',
    'entry.py'
)
if ($OneFile) { $args += '--onefile' } else { $args += '--onedir' }

& $python @args
if ($LASTEXITCODE -ne 0) { throw 'PyInstaller failed.' }

$target = if ($OneFile) { 'dist' } else { 'dist\WorkspaceChecker' }
$exe = Join-Path $target 'WorkspaceChecker.exe'

# Antivirus can lock the freshly written binary and leave PyInstaller's resource update
# half-applied, which yields an exe that fails only at launch. Prove it runs.
Write-Host 'Verifying executable...' -ForegroundColor Cyan
$probe = & $exe --version 2>&1
if ($LASTEXITCODE -ne 0 -or $probe -notmatch 'Workspace Checker') {
    throw "Built executable does not run: $probe"
}
Write-Host "  $probe" -ForegroundColor DarkGray

Copy-Item 'healthcheck.config.sample.json' $target -Force
Copy-Item 'README.md' $target -Force

$zip = 'dist\WorkspaceChecker.zip'
Remove-Item $zip -Force -ErrorAction SilentlyContinue
Compress-Archive -Path $target -DestinationPath $zip

Write-Host "Built $target" -ForegroundColor Green
Write-Host "Packaged $zip" -ForegroundColor Green
