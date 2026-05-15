#!/usr/bin/env sh
# Bootstrap uv environment for flood-ops on Linux / macOS.
#
# Usage:
#   ./uv-sync.sh                              # install philflood from git
#   ./uv-sync.sh --local ../GLOFAS_ImpactFloodForecasting_PHL
#
set -e

PYTHON_VERSION="3.11"
LOCAL_PHILFLOOD=""

while [ $# -gt 0 ]; do
    case "$1" in
        --local) LOCAL_PHILFLOOD="$2"; shift 2 ;;
        *) echo "Unknown option: $1"; exit 1 ;;
    esac
done

if ! command -v uv >/dev/null 2>&1; then
    echo "uv not found. Install from https://docs.astral.sh/uv/getting-started/installation/"
    exit 1
fi

echo "==> Installing Python $PYTHON_VERSION"
uv python install "$PYTHON_VERSION"

if [ ! -d ".venv" ]; then
    echo "==> Creating .venv"
    uv venv --python "$PYTHON_VERSION"
fi

PY=".venv/bin/python"

echo "==> Installing core scientific packages"
uv pip install --python "$PY" \
    "climada>=4.0.0,<6.1" \
    "climada-petals>=4.0.2,<5.0.0" \
    "pyextremes>=2.3.0" \
    "ipywidgets>=7.6.0"

if [ -n "$LOCAL_PHILFLOOD" ]; then
    echo "==> Installing philflood from local path: $LOCAL_PHILFLOOD"
    uv pip install --python "$PY" --no-deps -e "$LOCAL_PHILFLOOD"
else
    echo "==> Installing philflood from git"
    uv pip install --python "$PY" --no-deps \
        "philflood @ git+https://github.com/rodekruis/GLOFAS_ImpactFloodForecasting_PHL.git"
fi

echo "==> Installing flood-ops (editable)"
uv pip install --python "$PY" --no-deps -e .

# cfgrib must be installed separately (no binary wheel available on all platforms)
uv pip install --python "$PY" cfgrib

echo ""
echo "✓ Environment ready."
echo "  Activate:  source .venv/bin/activate"
echo "  Run:       python ops/pipeline/run_daily_monitoring_etl.py --help"
