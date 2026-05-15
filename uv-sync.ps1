<#
.SYNOPSIS
    Bootstrap a uv virtual environment for flood-ops on Windows.

.DESCRIPTION
    1. Installs Python 3.11 via uv (if not already present).
    2. Creates / reuses .venv.
    3. Installs philflood's heavy scientific dependencies before syncing
       (same pattern as the main GLOFAS repo, needed for climada).
    4. Installs flood-ops in editable mode.

.PARAMETER PythonVersion
    Python version for the venv (default: 3.11).
.PARAMETER RecreateVenv
    Delete and recreate .venv from scratch.
.PARAMETER DryRun
    Print commands without executing them.
.PARAMETER LocalPhilflood
    Path to a local checkout of GLOFAS_ImpactFloodForecasting_PHL.
    If provided, philflood is installed from that path instead of git.

USAGE
    # Standard setup
    .\uv-sync.ps1

    # With local philflood checkout
    .\uv-sync.ps1 -LocalPhilflood ..\GLOFAS_ImpactFloodForecasting_PHL

    # Recreate clean environment
    .\uv-sync.ps1 -RecreateVenv
#>
[CmdletBinding()]
param(
    [string]$PythonVersion    = "3.11",
    [switch]$RecreateVenv,
    [switch]$DryRun,
    [string]$LocalPhilflood   = ""
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

# Heavy deps that must be installed before uv-sync resolves philflood
$corePkgs = @(
    "climada>=4.0.0,<6.1",
    "climada-petals>=4.0.2,<5.0.0",
    "pyextremes>=2.3.0",
    "ipywidgets>=7.6.0"
)

if ($RecreateVenv -and (Test-Path ".venv")) {
    Invoke-Step "Remove existing .venv" { Remove-Item -Recurse -Force ".venv" } "Remove-Item -Recurse -Force .venv"
}

Invoke-Step "Install Python $PythonVersion" { uv python install $PythonVersion } "uv python install $PythonVersion"

if (-not (Test-Path ".venv")) {
    Invoke-Step "Create .venv" { uv venv --python $PythonVersion } "uv venv --python $PythonVersion"
}

Invoke-Step "Install core scientific packages" {
    uv pip install --python $pythonExe @corePkgs
} "uv pip install <core-pkgs>"

if ($LocalPhilflood) {
    $absPath = (Resolve-Path $LocalPhilflood).Path
    Invoke-Step "Install philflood from local path: $absPath" {
        uv pip install --python $pythonExe --no-deps -e $absPath
    } "uv pip install --no-deps -e $absPath"
} else {
    Invoke-Step "Install philflood from git" {
        uv pip install --python $pythonExe --no-deps `
            "philflood @ git+https://github.com/rodekruis/GLOFAS_ImpactFloodForecasting_PHL.git"
    } "uv pip install philflood @ git+..."
}

Invoke-Step "Install flood-ops (editable)" {
    uv pip install --python $pythonExe --no-deps -e .
} "uv pip install --no-deps -e ."

Write-Host "`n✓ Environment ready. Activate with:  .venv\Scripts\Activate.ps1" -ForegroundColor Green
Write-Host "  Then run:  python ops/pipeline/run_daily_monitoring_etl.py --help" -ForegroundColor Green
