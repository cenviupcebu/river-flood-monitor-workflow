#!/usr/bin/env sh
# Bootstrap uv environment for flood-ops on Linux / macOS.
#
# Flow:
#   1. Installs Python 3.11 via uv (if needed).
#   2. Creates / reuses .venv.
#   3. Runs uv sync to install project dependencies from pyproject.toml/uv.lock.
#   4. Installs cfgrib separately.
#
# Usage:
#   ./uv-sync.sh
#
set -e

PYTHON_VERSION="3.11"

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

# cfgrib must be installed separately (no binary wheel available on all platforms)
echo "==> Installing cfgrib"
uv pip install --python "$PY" cfgrib

echo ""
echo "  Environment ready."
echo "  Activate:  source .venv/bin/activate"
echo "  Run:       flood-monitoring --help"
