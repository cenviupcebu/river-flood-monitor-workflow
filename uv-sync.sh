#!/usr/bin/env sh
# Bootstrap uv environment for flood-ops on Linux / macOS.
#
# Flow:
#   1. Installs Python 3.11 via uv (if needed).
#   2. Creates / reuses .venv.
#   3. Runs uv sync to install project dependencies from pyproject.toml/uv.lock.
#   4. Optionally installs philflood from git or a local checkout.
#   5. Installs cfgrib separately.
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

echo "==> Syncing project dependencies"
uv sync --python "$PY"

if [ -n "$LOCAL_PHILFLOOD" ]; then
    echo "==> Installing philflood from local path: $LOCAL_PHILFLOOD"
    uv pip install --python "$PY" --no-deps -e "$LOCAL_PHILFLOOD"
else
    echo "==> Installing philflood from git"
    uv pip install --python "$PY" --no-deps \
        "philflood @ git+https://github.com/rodekruis/GLOFAS_ImpactFloodForecasting_PHL.git"
fi

# cfgrib must be installed separately (no binary wheel available on all platforms)
echo "==> Installing cfgrib"
uv pip install --python "$PY" cfgrib

echo ""
echo "✓ Environment ready."
echo "  Activate:  source .venv/bin/activate"
echo "  Run:       flood-monitoring --help"
