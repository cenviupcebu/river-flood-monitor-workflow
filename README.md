# River flood workflow

A Python tool for monitoring riverine forecast developed by NLRC to support Start Network Philippines.

## Setup

### 1. Install `uv`

`uv` is a Python tool for managing virtual environments and dependencies. It will create a `.venv` with Python 3.13 and all required packages for this project.

**Windows (PowerShell):**
```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

**macOS / Linux:**
```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Or via pip: `pip install uv`

### 2. Install dependencies

```bash
uv sync
```

This creates a `.venv` with Python 3.13 and all required packages.

### 3. Run the notebook

```bash
uv run jupyter lab
```