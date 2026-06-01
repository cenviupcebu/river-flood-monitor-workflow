<#
.SYNOPSIS
    Bootstrap a uv virtual environment for flood-ops on Windows.

.DESCRIPTION
    1. Installs Python 3.11 via uv (if not already present).
    2. Creates / reuses .venv.
    3. Runs uv sync to install all project dependencies from pyproject.toml/uv.lock.
    4. Installs cfgrib separately.

.PARAMETER PythonVersion
    Python version for the venv (default: 3.11).
.PARAMETER RecreateVenv
    Delete and recreate .venv from scratch.
.PARAMETER DryRun
    Print commands without executing them.

USAGE
    # Standard setup
    .\uv-sync.ps1

    # Recreate clean environment
    .\uv-sync.ps1 -RecreateVenv
#>
[CmdletBinding()]
param(
    [string]$PythonVersion    = "3.11",
    [switch]$RecreateVenv,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"

function Invoke-Step {
    param([string]$Description, [scriptblock]$Command, [string]$Preview)
    Write-Host "`n==> $Description" -ForegroundColor Cyan
    if ($DryRun) { Write-Host "DRY-RUN: $Preview" -ForegroundColor Yellow; return }
    & $Command
}

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    throw "uv not found. Install from https://docs.astral.sh/uv/getting-started/installation/"
}

$pythonExe = ".venv/Scripts/python.exe"

if ($RecreateVenv -and (Test-Path ".venv")) {
    Invoke-Step "Remove existing .venv" { Remove-Item -Recurse -Force ".venv" } "Remove-Item -Recurse -Force .venv"
}

Invoke-Step "Installing Python $PythonVersion" { uv python install $PythonVersion } "uv python install $PythonVersion"

if (-not (Test-Path ".venv")) {
    Invoke-Step "Creating .venv" { uv venv --python $PythonVersion } "uv venv --python $PythonVersion"
}

Invoke-Step "Syncing project dependencies" {
    uv sync --python $pythonExe
} "uv sync --python $pythonExe"

Invoke-Step "Installing cfgrib" {
    uv pip install --python $pythonExe cfgrib
} "uv pip install --python $pythonExe cfgrib"

Write-Host ""
Write-Host "  Environment ready." -ForegroundColor Green
Write-Host "  Activate:  .venv\Scripts\Activate.ps1" -ForegroundColor Green
Write-Host "  Run:       flood-monitoring --help" -ForegroundColor Green
