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

### 3. Run the workflow

#### Option 1: Run the CLI tool

```bash
uv run python ops/pipeline/run_daily_monitoring_etl.py `
    --date 2026-05-15 `
    --run-spec config/run_specs/daily_monitoring_etl.template.yaml `
    --basins config/basins/Cagayan_01.yaml
```

#### Option 2: Run Jupyter notebook

```bash
uv run jupyter lab
```

### Repo structure:

```
flood-ops/                         ← new repo root
├── pyproject.toml                 depends on philflood as git/path source
├── .python-version                3.11
├── .gitignore
├── uv-sync.ps1                    Windows bootstrap
├── uv-sync.sh                     Linux/macOS bootstrap
│
├── src/flood_ops/
│   ├── __init__.py
│   ├── cli.py                     console-script: flood-ops-daily
│   └── etl/
│       ├── __init__.py            orchestrator (run_daily_monitoring_etl)
│       ├── utils.py               shared dataclasses + expand_template
│       ├── run_spec.py            YAML loader + settings dataclasses
│       ├── step1_ingest.py        resolve forecast path
│       ├── step2_detect.py        stub (NB07 detection — v1.0)
│       ├── step3_impact.py        load precomputed impact cube
│       ├── step4_evaluate.py      OEP thresholds + exceedance probabilities
│       ├── step5_decide.py        tier rules + persistence + min-lead
│       └── step6_output.py        JSON / CSV writer
│
├── ops/
│   ├── pipeline/
│   │   ├── run_daily_monitoring_etl.py   thin shim → flood_ops.cli
│   │   └── run_monitoring_scheduled.py   24-h loop runner
│   └── configs/
│       ├── basins/Cagayan_01.yaml
│       └── run_specs/
│           ├── daily_monitoring_etl.template.yaml
│           └── precomputed_impacts.example.json
│
└── tests/
    └── test_etl_rules.py
```