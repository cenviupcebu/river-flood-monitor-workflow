# River flood workflow

A Python tool for monitoring riverine forecast developed by NLRC to support Start Network Philippines.

Code development assisted by: [Copilot]

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

This creates a `.venv` with all required runtime dependencies (including
scientific packages such as numpy, pandas, xarray, scipy, geopandas, and rasterio).

For cross-platform bootstrap helpers that also install Python and `cfgrib`:

```powershell
.\uv-sync.ps1
```

```bash
./uv-sync.sh
```

### 3. Run the workflow

#### Option 1: Run the CLI tool

```bash
uv run flood-monitoring `
    --date 2026-05-15 `
    --run-spec config/run_specs/daily_monitoring_etl.yaml `
    --basins config/basins/Cagayan_01.yaml
```

#### Option 2: Run Jupyter notebook

```bash
uv run jupyter lab
```

For automated daily runs, schedule the CLI command with an external scheduler
(Windows Task Scheduler, cron, Airflow, Azure ML schedule).

### Repo structure:

```
river-flood-workflow/
├── pyproject.toml                 project dependencies
├── uv.lock                        locked dependency versions
├── uv-sync.ps1                    Windows bootstrap
├── uv-sync.sh                     Linux/macOS bootstrap
├── workflow.ipynb                 notebook workflow
│
├── config/
│   ├── config.yaml                project-level config
│   ├── basins/
│   │   └── Cagayan_01.yaml
│   └── run_specs/
│       ├── daily_monitoring_etl.yaml
│       ├── daily_monitoring_etl.template.yaml
│       └── precomputed_impacts.example.json
│
├── data/
│   ├── admin-areas/
│   ├── bronze/
│   ├── silver/
│   └── gold/
├── data_update/
│   ├── download_jrc_flood_tiles.py
│   └── README.md
├── logs/
│   └── YYYY/MM/*.txt              run logs by month
│
├── src/flood_ops/
│   ├── __init__.py
│   ├── cli.py                     console-script: flood-monitoring
│   ├── cli_step_flags.py          pipeline step toggles
│   ├── config.py                  config loading helpers
│   ├── logging.py                 logging helpers
│   └── etl/
│       ├── __init__.py
│       ├── extract.py
│       ├── extract-example.py
│       ├── forecast.py
│       ├── pipeline.py
│       ├── pipeline_step_flags.py
│       ├── prepare.py
│       ├── run_spec.py
│       ├── save.py
│       └── utils.py
│
└── tests/
    ├── test_etl_rules.py
    └── test_step3_impact.py
```