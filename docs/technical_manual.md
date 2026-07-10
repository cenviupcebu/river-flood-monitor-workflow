# River Flood Workflow — Technical Manual for Maintainers

---

## 1. General Introduction

### 1.1 Purpose and scope

River Flood Workflow is a Python ETL pipeline developed by NLRC to support Start Network Philippines. It converts GloFAS ensemble river discharge forecasts into operational flood-trigger decisions at municipality (ADM3) level, and is designed for daily automated monitoring. The goal is to give operational responders a timely, evidence-based signal that a flood event may exceed pre-defined impact thresholds in specific municipalities, so that early action can be taken.

From a code perspective, the workflow is structured as a three-stage ETL pipeline (extract, forecast, save) driven by a YAML run specification. Each stage is independently executable, which is important for debugging and incremental data refreshes. The codebase lives entirely under `src/river_flood_monitoring/` and has no runtime dependency on external proprietary packages.

At the top level, the workflow:
- reads GloFAS ensemble NetCDF discharge forecasts for a given issue date
- loads EVT calibration parameters and OEP impact thresholds per municipality
- estimates flood extents and exposed population per municipality across all ensemble members and lead days
- evaluates three activation tiers (T1, T2, T3) using probability and persistence rules
- writes trigger outputs, decision summaries, and maps for operational consumption

### 1.2 Runtime architecture

The pipeline is invoked via a console script registered in `pyproject.toml`. The CLI parses arguments and delegates to the pipeline orchestrator, which coordinates the three ETL stages and file-based caching. Understanding this layered design is important when tracing failures: an error surfaced in the CLI log nearly always originates one or two levels deeper, in a stage module or a spatial helper.

Main CLI entry point:
- `flood-monitoring` (implemented in `src/river_flood_monitoring/cli.py`)

Pipeline orchestrator:
- `run_daily_monitoring_etl()` in `src/river_flood_monitoring/etl/pipeline.py`

Pipeline stages:
- extract → `src/river_flood_monitoring/etl/extract.py`
- forecast → `src/river_flood_monitoring/etl/forecast.py`
- save → `src/river_flood_monitoring/etl/save.py`

Supporting modules:
- `run_spec.py` — YAML loader and typed settings dataclasses
- `utils.py` — shared dataclasses (`TierDecision`, `UnitDecision`, `BasinRunOutput`, `expand_template`)
- `logging.py` — log format setup and file handler attachment
- `config.py` — basin name validation and `BasinConfig` factory

### 1.3 Execution modes

The pipeline supports a full end-to-end run as well as individual stage runs for debugging or partial reruns. When no step flag is given, all three stages run in order. When a step flag is set, only the selected stages run and intermediate artifacts are loaded from the cache directory for any upstream stages that were skipped. This caching mechanism lets maintainers replay only the failed stage without re-running expensive spatial computations.

Default (full run): extract → forecast → save

Selective step flags:
- `--extract` — run only the extract stage
- `--forecast` — run only the forecast stage (requires cached extract artifact)
- `--save` — run only the save stage (requires cached forecast artifact)

Intermediate artifacts are cached under:
- `data/etl_step_cache/<run_name>/<issue_date>/extract/<basin>.json`
- `data/etl_step_cache/<run_name>/<issue_date>/forecast/<basin>.json`
- `data/etl_step_cache/<run_name>/<issue_date>/run_manifest.json`

### 1.4 Supported operational basins

The set of allowed basins is defined statically in `src/river_flood_monitoring/config.py` in the `ALLOWED_BASINS` list. Adding a new basin requires updating this list and providing the corresponding OEP JSON and EVT Parquet files. Currently:
- `cagayan`

---

## 2. Methodology

### 2.1 Pipeline step overview

The three pipeline stages represent a clean separation of concerns: extract isolates I/O and data validation, forecast contains all scientific computation, and save handles serialisation and visual outputs. This design makes it straightforward to test or replace individual stages without disturbing the others.

**Extract** — resolves file paths and loads thresholds:
- Resolves GloFAS NetCDF file paths for the issue date using the `ingest.forecast_path_template` glob pattern
- Loads OEP impact thresholds from the basin JSON and filters units below the `oep_min` population exposure floor
- Packages paths and settings into a compact dictionary passed to forecast

**Forecast** — spatial detection, impact estimation, rule evaluation:
- Converts discharge to return period using EVT/GPD parameters per grid cell
- Detects spatially contiguous flood patches for each ensemble member and lead day
- Derives event flood depth rasters by interpolating JRC static hazard maps to the event's return period
- Aggregates exposed population per ADM3 unit by overlaying depth rasters and WorldPop
- Computes ensemble exceedance probabilities per unit, lead, and return period
- Applies T1/T2/T3 tier rules with persistence and minimum-lead constraints

**Save** — serialises decisions and produces outputs:
- Flattens all tier decisions into a timestamped CSV
- Conditionally writes a `decision.txt` summary flag when any tier fires
- Generates colour-coded activated-area and population-exposed maps per basin and lead day

### 2.2 Forecast stage: detailed process

The forecast stage is the computational heart of the workflow. It translates raw discharge numbers into operational alert decisions through a chain of spatial and probabilistic steps. Each step is designed to be independently inspectable: intermediate rasters are written to a temporary directory, and the impact cube can be traced through the log output at DEBUG level.

#### 2.2.1 Discharge to return period conversion

Each GloFAS grid cell in the basin has EVT calibration parameters fitted from historical peak-over-threshold (POT) data. These are stored in the EVT Parquet file and loaded at the start of the forecast stage. For each ensemble member and lead day, the discharge value at each cell is converted to a return period using the Poisson-GPD exceedance rate formula.

Parameters per cell (columns in the EVT Parquet):
- `u` — POT threshold (m³/s)
- `sigma` — GPD scale parameter
- `xi` — GPD shape parameter
- `lam` — Poisson rate (events per year)

A cell is considered "active" when its return period is at or above the configured `detection.t0_years` threshold. The conversion is implemented in `_gpd_exceedance_rate()` and `discharge_to_return_period()` in `forecast.py`.

#### 2.2.2 Flood patch detection

Active cells are grouped into spatially contiguous patches using connected-component labelling (`scipy.ndimage.label`). Connectivity is configurable (4-neighbour von Neumann or 8-neighbour Moore) via `detection.cc_connectivity`. Each detected component is area-filtered: patches smaller than `detection.a_min_km2` (km²) are discarded. Only patches that pass this filter are passed to the depth-raster rendering step. This prevents small isolated noise cells from generating spurious impact estimates.

The function `_detect_flood_patches_for_lead()` returns, for each ensemble member, a list of patch dictionaries containing the valid date, approximate area, and bounding box.

#### 2.2.3 Flood depth derivation from JRC static maps

For each qualifying patch, a temporary flood depth raster is constructed by interpolating between the JRC return-period depth GeoTIFFs. The JRC maps cover return periods RP10, RP20, RP50, RP75, RP100, RP200, and RP500. The interpolation is log-linear in return period space. The result is clipped to the patch bounding box to reduce memory and avoid contaminating neighbouring basin units.

The function `_render_depth_raster_for_patch()` writes each depth raster as a temporary GeoTIFF to a `tempfile.TemporaryDirectory` that is cleaned up at the end of the lead-day loop.

#### 2.2.4 Population exposure calculation

Population exposure is estimated by overlaying each depth raster with the WorldPop population density grid and a rasterised version of the ADM3 administrative boundaries. Both are aligned to the same spatial grid (WorldPop CRS and resolution) in `_load_spatial_resources()`. The depth threshold `detection.depth_threshold_m` (default 0.02 m) acts as a minimum depth for counting a population cell as affected.

The function `_aggregate_population_from_arrays()` accumulates exposed population counts per integer admin unit ID. These are converted to the `ADM3::<pcode>` unit key format and accumulated into the `ImpactCube` data structure:

```
ImpactCube = {unit_id: {lead_day: {member_id: {rp: affected_people}}}}
```

The function `_compute_impacts_from_event_patches()` drives this aggregation across all patches for a given lead day and merges the results into the cube.

#### 2.2.5 Exceedance probability and tier evaluation

Once the ImpactCube is populated, `_compute_prob_exceed()` computes, for each unit, lead day, and return period, the fraction of ensemble members whose population impact exceeds the OEP threshold. This gives a probability between 0 and 1.

The function `_apply_tier_rules()` then evaluates each tier rule against these probabilities:
- T1 fires when exceedance probability ≥ 0.50 at RP2
- T2 fires when exceedance probability ≥ 0.50 at RP5
- T3 fires when exceedance probability ≥ 0.35 at RP10

Firing is conditional on persistence: `_find_latest_persistent_lead()` checks that the probability threshold is met on a contiguous window of `decision.persist_days` consecutive lead days, and that the selected lead day is at or above `decision.min_lead`. The highest qualifying lead day is reported as `fire_lead`, providing the maximum available warning time.

---

## 3. Data Description

### 3.1 Required input data

The workflow depends on two categories of input data: dynamic forecast data that changes daily, and static reference data that is updated infrequently. Maintainers should pay particular attention to the path templates in the run spec, since most runtime failures originate from missing or misnamed files rather than code bugs.

**Dynamic inputs (updated per forecast cycle):**

1. GloFAS ensemble NetCDF forecast files
   - Path pattern: `data/bronze/glofas/forecast/dis_{ens_no}_{yyyymmdd}00.nc`
   - One file per ensemble member (up to 51 members, numbered 00–50)
   - Variable: `dis` (river discharge, m³/s) with dimensions lat, lon, valid_time
   - Source: Copernicus Emergency Management Service — GloFAS
   - Website: https://global-flood.emergency.copernicus.eu/

**Static reference inputs (updated infrequently):**

2. JRC global flood hazard depth maps
   - Path root: `data/bronze/jrc_flood_maps/`
   - Sub-directories named `RP10/`, `RP20/`, `RP50/`, `RP75/`, `RP100/`, `RP200/`, `RP500/`
   - Each sub-directory contains GeoTIFF tiles (`*_depth.tif`) for that return period
   - Source: JRC CEMS-GLOFAS flood hazard products
   - Website: https://jeodpp.jrc.ec.europa.eu/ftp/jrc-opendata/CEMS-GLOFAS/flood_hazard/
   - Update tool: `data_update/download_jrc_flood_tiles.py`

3. Population raster (WorldPop)
   - Path example: `data/bronze/population/phl_ppp_2020.tif`
   - Format: GeoTIFF, EPSG:4326, units: people per pixel
   - Source: WorldPop gridded population (constrained, top-down estimate)
   - Website: https://www.worldpop.org/

4. Administrative boundaries (ADM3)
   - Path example: `data/admin-areas/phl_admin3.geojson`
   - Format: GeoJSON, EPSG:4326
   - Required attribute: `adm3_pcode` (configurable via `inputs.adm3_unit_column`)
   - Used for spatial aggregation and map rendering

**Configuration inputs (calibration artefacts, not updated per run):**

5. OEP risk profiles
   - Path template: `config/risk_profiles/{basin}_oep_curves_all_units.json`
   - JSON schema: `{ "rp_report": [...], "units": [{"pcode": ..., "oep_rl": [...], ...}] }`
   - Contains per-municipality OEP impact thresholds (affected people) for each return period

6. EVT calibration parameters
   - Path template: `config/risk_profiles/{basin}_evt_pot_calibration.parquet`
   - Parquet schema: one row per GloFAS grid cell with columns `cell_id`, `u`, `lam`, `xi`, `sigma`, plus `lat` and `lon` (or derivable from `cell_id`)
   - Used to convert discharge to return period during the forecast stage

### 3.2 Workflow outputs

All outputs land under a dated directory created by the save stage at runtime. The timestamp in file names ensures that multiple runs for the same issue date do not overwrite each other, which is useful for auditing re-runs.

Output root: `data/gold/trigger_decisions/<YYYY-MM-DD>/`

Files produced:

1. `trigger_decisions_<date>_<timestamp>.csv`
   - One row per basin × ADM3 unit × tier
   - Columns: `issue_date`, `basin_name`, `level`, `name`, `pcode`, `tier`, `rp`, `p_threshold`, `fired`, `fire_lead`, `probability_at_fire`, `impact_population_threshold`, `impact_population_at_fire`
   - Always written, regardless of whether any tier fired

2. `decision.txt`
   - Content: `triggered=True`
   - Written only when at least one tier fires (`total_fired > 0`)
   - Intended as a simple machine-readable flag for downstream automation

3. `maps/<basin>_lead<lead>_activated_map.png`
   - One map per (basin, fire_lead) pair
   - Shows all ADM3 units in the basin with activated units colour-coded by tier

4. `maps/<basin>_lead<lead>_population_exposed_map.png`
   - One map per (basin, fire_lead) pair
   - Shows activated units shaded by the ensemble-mean exposed population at the fire lead day

### 3.3 Output scenarios

Understanding which outputs are produced under which conditions is important when diagnosing apparent failures. The CSV is always written — its absence is a reliable signal that the save stage itself crashed. Missing maps or a missing `decision.txt` file is expected behaviour under no-trigger conditions, not a bug.

**Scenario A — At least one tier fires (`total_fired > 0`):**
- CSV trigger decisions file is written
- `decision.txt` is written with content `triggered=True`
- Activated-area and population-exposed maps are generated for each (basin, fire_lead) combination
- Log line: `Decision output written: ... (triggered=True)`

**Scenario B — No tiers fire (`total_fired = 0`):**
- CSV trigger decisions file is written (all `fired` columns are `False`)
- `decision.txt` is not written; maps are not generated
- Log line: `No activated tiers found; skipping decision summary file`

Maintenance note: `tests/test_save_decision.py` currently expects `decision.txt` to contain `triggered=False` when no tiers fire, but `save.py` skips writing the file entirely in that scenario. This is a known code-vs-test inconsistency that should be resolved before using those tests as release gates.

### 3.4 Map rendering details

Both map types share the same spatial extent — all ADM3 units belonging to the queried basin — so the maps provide full context even when only a subset of municipalities activate. The selection of the highest tier per unit ensures that the map is not cluttered by redundant lower-tier activations.

Activated-area map:
- All basin ADM3 boundaries drawn as outlines in dark grey (`#4D4D4D`)
- Activated units filled by their highest firing tier, using the following colour encoding:
  - T1 (Moderate Watch): `#FFD54F` (amber yellow)
  - T2 (High Alert): `#FB8C00` (orange)
  - T3 (Very High Activation): `#D32F2F` (dark red)
- Title format: `<basin_name> | activated alerts at lead day <fire_lead>`

Population exposed map:
- All basin ADM3 boundaries drawn as outlines
- Only activated units with a valid `impact_population_at_fire` value are filled
- Colour scale: `YlOrRd` (yellow-orange-red), continuous, legend labelled "Population exposed"
- Title format: `<basin_name> | population exposed at lead day <fire_lead>`

Tier and lead selection logic:
- For each (basin, fire_lead, pcode) group, only the highest-ranking fired tier is plotted (T1 < T2 < T3)
- Maps are generated per unique `fire_lead` value among all fired units; each lead day produces a separate set of map files

---

## 4. Setup and Compatibility

### 4.1 Python version and dependency management

The project uses `uv` as its Python and dependency manager. `uv` creates an isolated virtual environment at `.venv/` and installs all dependencies from the locked `uv.lock` file, which ensures reproducible environments across machines. Maintainers should not install dependencies manually with `pip` into the system Python, as geospatial packages (rasterio, geopandas, xarray) require binary extensions that `uv` handles automatically through pre-built wheels.

Declared compatibility in `pyproject.toml`:
- `requires-python = ">=3.9,<3.13"`
- Tested locally with Python 3.12

Installing the environment from scratch:
```bash
uv sync
```

Bootstrap scripts that also install Python and cross-platform geospatial dependencies:
- Linux/macOS: `./uv-sync.sh`
- Windows PowerShell: `.\uv-sync.ps1`

### 4.2 Key runtime dependencies

The following packages are worth understanding for debugging purposes, as failures in spatial computations almost always involve one of them:
- `xarray` — reads NetCDF forecast files lazily; version mismatches can produce unexpected dimension orderings
- `rasterio` — reads and writes GeoTIFF depth rasters; requires GDAL shared libraries
- `geopandas` / `shapely` — reads ADM3 boundaries and performs spatial operations
- `scipy.ndimage` — connected-component labelling for flood patch detection
- `pandas` / `pyarrow` — reads the EVT calibration Parquet and builds the trigger CSV

### 4.3 OS compatibility

The codebase is designed to run on both Linux and Windows. Path handling uses `pathlib.Path` throughout, which is platform-agnostic. Log paths use forward-slash templates that are expanded at runtime. The primary operational target is Linux (for scheduled deployments), but Windows is fully supported for development and debugging.

For Linux deployments:
- Prefer scheduling via `cron` or `systemd` with `uv run` as the execution prefix
- Ensure the GDAL C libraries are available in the system environment (`libgdal-dev` or equivalent)
- Log output directories are auto-created by the pipeline; no manual setup required

---

## 5. Key Commands for Maintainers

### 5.1 Running the workflow

These are the most frequently used commands. A maintainer should be comfortable running selective steps to narrow down failures without executing expensive spatial computations unnecessarily.

Full end-to-end run (all three stages):
```bash
uv run flood-monitoring \
    --run-spec config/run_specs/daily_monitoring_etl.yaml \
    --basins cagayan
```

Run for a specific historical issue date:
```bash
uv run flood-monitoring \
    --run-spec config/run_specs/daily_monitoring_etl.yaml \
    --basins cagayan \
    --date 2026-06-01
```

Run multiple basins simultaneously (when more basins are added):
```bash
uv run flood-monitoring \
    --run-spec config/run_specs/daily_monitoring_etl.yaml \
    --basins cagayan abra ilocos
```

### 5.2 Selective stage execution (for debugging)

When a run fails, start by identifying which stage failed from the log, then replay only that stage using its flag. Upstream cached artifacts from the last successful run are read automatically.

Run extract only:
```bash
uv run flood-monitoring \
    --run-spec config/run_specs/daily_monitoring_etl.yaml \
    --basins cagayan --extract
```

Run forecast only (reads extract artifact from cache):
```bash
uv run flood-monitoring \
    --run-spec config/run_specs/daily_monitoring_etl.yaml \
    --basins cagayan --forecast
```

Run save only (reads forecast artifact from cache):
```bash
uv run flood-monitoring \
    --run-spec config/run_specs/daily_monitoring_etl.yaml \
    --basins cagayan --save
```

### 5.3 Running tests

```bash
uv run pytest
```

```bash
uv run pytest tests/test_etl_rules.py -v
```

### 5.4 Refreshing static data

Download or refresh JRC flood hazard tiles for a bounding box:
```bash
uv run python data_update/download_jrc_flood_tiles.py \
    --bbox 117.0 14.5 123.5 19.5
```

---

## 6. Detailed Step and Subfunction Guide

This section provides a function-level reference for the three ETL stages. For each function, the inputs, outputs, and internal behaviour are described in enough detail to allow a maintainer to understand what state the pipeline is in at any point during execution, and to diagnose failures by inspecting intermediate data or log output.

### 6.1 Extract stage (`etl/extract.py`)

The extract stage is intentionally lightweight — it resolves file paths, validates their existence, and loads configuration data. No spatial computation happens here. This means that if the extract stage succeeds but forecast fails, the failure is almost certainly a data-content problem (wrong schema, missing grid cells) rather than a path resolution problem.

---

#### `extract(config, issue_date, run_spec) -> Dict[str, Any]`

**Purpose:** Top-level entry point for the extract stage. Orchestrates path resolution, OEP loading, and packaging of all inputs required by forecast.

**Inputs:**
- `config` — `BasinConfig` dataclass with field `basin_name` (string, e.g. `"cagayan"`)
- `issue_date` — `datetime.date`, the forecast initialisation date
- `run_spec` — `PipelineRunSpec` dataclass loaded from YAML; must have non-None `inputs` block

**Outputs — returned dictionary keys:**
- `basin_name` (str) — basin identifier, forwarded unchanged from `config`
- `forecast_paths` (List[str]) — sorted list of absolute paths to matched NetCDF files
- `oep_path` (Path) — path to the OEP JSON file for this basin
- `thresholds` (Dict[str, Dict[int, float]]) — `{unit_id: {rp: threshold_people}}` for all qualifying units
- `unit_metadata` (Dict[str, Dict[str, str]]) — `{unit_id: {level, name, pcode}}` for all qualifying units
- `evt_parquet` (Path) — path to the EVT calibration Parquet file
- `det` (DetectionSettings) — detection hyper-parameters forwarded from the run spec

**Failure behaviour:** Raises `ValueError` if `run_spec.inputs` is None. Raises `FileNotFoundError` if no forecast files match the template.

---

#### `_resolve_forecast_path(run_spec, issue_date) -> Optional[List[str]]`

**Purpose:** Resolves the `ingest.forecast_path_template` to a sorted list of matching NetCDF file paths on disk. This function handles both ensemble-template patterns (with `{ens_no}` or `{ens}` placeholders) and fixed-token patterns (e.g. `dis_00_YYYYMMDD00.nc`, where `00` is expanded to a glob `*`).

**Inputs:**
- `run_spec` — `PipelineRunSpec`; uses `run_spec.ingest.forecast_path_template`
- `issue_date` — `datetime.date`; used to substitute `{yyyymmdd}`, `{yyyy}`, `{mm}`, `{dd}` placeholders

**Outputs:**
- `List[str]` of absolute path strings to matched files, sorted lexicographically
- `None` if no ingest settings are defined or no files are found

**Internal logic:**
- If the template contains `{ens}` or `{ens_no}`, those are replaced with `*` to produce a glob pattern
- If neither placeholder is present, the function attempts to expand a fixed `_00_` token to `_*_` (one substitution only)
- The parent directory is globbed and results filtered to files only
- If `download_if_missing=True` is set and no files are found, raises `NotImplementedError` (download not yet implemented)

**Log messages to watch:**
- `INFO: Forecast files found: N match(es)` — success
- `WARNING: Forecast file(s) not found: <pattern>` — template mismatch or missing data

---

#### `_load_oep_thresholds(oep_json_path, oep_min) -> (thresholds, unit_metadata)`

**Purpose:** Parses the OEP JSON file and builds per-unit threshold dictionaries. Units with an OEP RP2 impact below `oep_min` are excluded from the result, which prevents municipalities with negligible modelled exposure from ever triggering.

**Inputs:**
- `oep_json_path` (Path) — path to `{basin}_oep_curves_all_units.json`
- `oep_min` (float) — minimum RP2 exposed population for a unit to qualify (from `decision.oep_min`)

**Outputs (two-element tuple):**
- `thresholds`: `Dict[str, Dict[int, float]]` — `{"ADM3::<pcode>": {2: 180.0, 5: 340.0, 10: 520.0}}`
- `unit_metadata`: `Dict[str, Dict[str, str]]` — `{"ADM3::<pcode>": {"level": "ADM3", "name": "Solana", "pcode": "PH020702000"}}`

**Expected JSON schema:**
```json
{
  "rp_report": [2, 5, 10, 20],
  "units": [
    { "pcode": "PH020702000", "level": "ADM3", "name": "Solana",
      "oep_rl": [180.0, 340.0, 520.0, 700.0] }
  ]
}
```

**Log messages to watch:**
- `INFO: OEP thresholds loaded: N qualifying units (from M total, oep_min=100)` — normal
- A low qualifying count relative to total units may indicate a high `oep_min` value or data quality issue

---

### 6.2 Forecast stage (`etl/forecast.py`)

The forecast stage is the most computationally intensive part of the pipeline. It processes all ensemble members across all lead days, making it both the slowest stage and the most likely to surface data-quality issues from the input files. When debugging, the log output from this stage is rich — most internal functions emit INFO-level progress messages that allow a maintainer to track exactly how far execution progressed before a failure.

---

#### `forecast(extracted, issue_date, run_spec) -> Dict[str, Any]`

**Purpose:** Top-level entry point for the forecast stage. Calls `detect_flood_events()` to build the ImpactCube, then runs exceedance probability computation and tier evaluation.

**Inputs:**
- `extracted` (Dict) — the dictionary returned by `extract()`; must contain `forecast_paths`, `evt_parquet`, `oep_path`, `thresholds`, `unit_metadata`, `det`
- `issue_date` — `datetime.date`
- `run_spec` — `PipelineRunSpec`; uses `run_spec.decision` for lead range and tier rules

**Outputs — returned dictionary keys:**
- `basin_name` (str)
- `forecast_paths` (List[str]) — forwarded from extracted
- `oep_path` (Path) — forwarded from extracted
- `units` (List[UnitDecision]) — one `UnitDecision` per qualifying ADM3 unit, each containing a list of `TierDecision` objects
- `impacts_source` (str) — provenance string, e.g. `"detect_phase:data/bronze/glofas/forecast/dis_00_2026060100.nc,..."`

---

#### `_build_lead_days_list(min_lead, max_lead) -> List[int]`

**Purpose:** Constructs the list of integer lead days to evaluate, from `decision.min_lead` to `decision.max_lead` inclusive.

**Inputs:** `min_lead` (int), `max_lead` (int) — both from `DecisionSettings`

**Output:** `[1, 2, 3, 4, 5]` for min_lead=1, max_lead=5

**Raises:** `ValueError` if `min_lead < 1` or `max_lead < min_lead`

---

#### `detect_flood_events(forecast_paths, ..., settings, lead_days_list) -> (ImpactCube, members, leads)`

**Purpose:** Main spatial detection loop. For each lead day in `lead_days_list`, detects flood patches across all ensemble members and computes the impact cube. This is the function that drives the most I/O and memory usage.

**Inputs:**
- `forecast_paths` — list of paths to NetCDF files (one per ensemble member)
- `evt_params_path` — path to the EVT Parquet file
- `oep_json_path` — path to the OEP JSON (used to initialise unit keys in the cube)
- `issue_date` — `datetime.date`
- `basin_name` — string, used for logging and temporary file naming
- `settings` — `DetectionSettings` dataclass
- `lead_days_list` — list of integer lead days

**Outputs (three-element tuple):**
- `ImpactCube`: `{unit_id: {lead_day: {member_id: {rp: affected_people}}}}`
- `members`: `List[int]` — sorted list of ensemble member IDs found in the forecast files
- `lead_days`: `List[int]` — the input `lead_days_list`

**Key caution:** The function currently applies `q_vals = q_vals * 10` as a temporary discharge multiplier to simulate trigger conditions during development. This multiplier will produce higher-than-realistic return period estimates and should be removed before production deployment.

---

#### `_open_forecast_source(forecast_paths, ...) -> (source, nc_index, lat_vals, lon_vals)`

**Purpose:** Indexes all provided NetCDF files without loading discharge data into memory. Builds a lookup table mapping `(valid_time, member_id)` to `(file_path, time_index)` for on-demand reading.

**Inputs:**
- `forecast_paths` — list of `Path` objects to NetCDF files
- `forecast_filename_example` — optional string; if provided, used to derive the per-member filename token

**Outputs:**
- `source` (dict) — contains `file_paths`, `var_name` (`"dis"`), `time_dim` (`"valid_time"` or `"time"`)
- `nc_index` (dict) — contains `msg_index`, `vt_lookup`, `members`, `steps`
- `lat_vals` (np.ndarray) — 1-D array of latitude values from the first file
- `lon_vals` (np.ndarray) — 1-D array of longitude values from the first file

**Raises:** `RuntimeError` if the `dis` variable is not found, or if required dimensions (lat, lon, time) are missing.

---

#### `_build_netcdf_index(file_paths, var_name, time_dim, ...) -> dict`

**Purpose:** Iterates over all provided NetCDF files and maps each `(valid_time, member_id)` pair to the corresponding `(file_path, time_index)`. The index enables on-demand, memory-efficient reading of individual time slices during the flood detection loop.

**Inputs:**
- `file_paths` — list of `Path` objects
- `var_name` — NetCDF variable name (always `"dis"`)
- `time_dim` — dimension name (`"valid_time"` or `"time"`)
- `forecast_filename_example` — optional, used to match member numbers from filenames

**Output:** dict with keys `msg_index` (the `(vt, member) → (file, t_idx)` lookup), `members` (sorted list), `steps` (set of step offsets in hours), `vt_lookup`

---

#### `_read_forecast_snapshot(source, msg_index, vt_lookup, valid_date, member, ...) -> Optional[np.ndarray]`

**Purpose:** Opens a single NetCDF file and reads the discharge slice for a specific valid time and member. Returns discharge values at the spatial indices of the basin cells.

**Inputs:**
- `valid_date` — `pd.Timestamp`
- `member` — integer ensemble member ID
- `cell_lat_idx`, `cell_lon_idx` — precomputed integer arrays mapping basin cell coordinates to NetCDF grid indices

**Output:** `np.ndarray` of shape `(n_cells,)` with discharge values in m³/s, or `None` if the key is not found in the index

---

#### `_detect_flood_patches_for_lead(forecast_source, ..., t0_years, a_min_km2, ...) -> (flood_members, patches_by_member)`

**Purpose:** For a single lead window, identifies which ensemble members produce spatially qualifying flood patches. A member is a "flood member" if at least one contiguous group of active cells exceeds the minimum area threshold.

**Inputs (key parameters):**
- `lead_window` — list of `pd.Timestamp` objects covering the lead day range
- `basin_cells` — list of cell_id strings from the EVT Parquet
- `evt_params` — DataFrame with EVT calibration parameters
- `t0_years` — minimum return period to mark a cell as active
- `a_min_km2` — minimum contiguous patch area in km²
- `connectivity` — 1 (4-neighbour) or 2 (8-neighbour, Moore)

**Outputs:**
- `flood_members` (List[int]) — member IDs that produced at least one qualifying patch
- `patches_by_member` (Dict[int, List[dict]]) — per-member list of patch metadata dicts, each with keys `valid_date`, `area_km2`, `bbox (lon_min, lat_min, lon_max, lat_max)`

---

#### `_render_depth_raster_for_patch(discharge_per_cell, evt_params, spatial, bbox, patch_bbox, out_tif) -> bool`

**Purpose:** Constructs a flood depth GeoTIFF for a single detected patch by interpolating between the JRC static hazard maps to the event's estimated return period.

**Inputs:**
- `discharge_per_cell` — `pd.Series` indexed by cell_id with discharge values (m³/s)
- `evt_params` — DataFrame with EVT parameters
- `spatial` — dict containing `rp_to_files` (RP → list of JRC tile paths)
- `bbox` — basin bounding box `(lon_min, lat_min, lon_max, lat_max)` for tile merging
- `patch_bbox` — patch bounding box for masking out-of-patch cells
- `out_tif` — `Path` to the output temporary GeoTIFF

**Output:** `bool` — `True` if the raster was written successfully, `False` if the patch was skipped (e.g. all JRC tiles unavailable, no finite depth values)

**Interpolation rule:**
- If event RP ≤ lowest available JRC RP: use the lowest RP map
- If event RP ≥ highest available JRC RP: use the highest RP map (capped at RP500)
- Otherwise: log-linear interpolation between the two bracketing RP maps

---

#### `_compute_impacts_from_event_patches(patches, worldpop_tif, depth_threshold_m) -> (members, leads, ImpactCube)`

**Purpose:** Iterates over all `EventPatchImpactInput` objects for a lead day, calls the population aggregator for each patch, and accumulates results into the ImpactCube.

**Inputs:**
- `patches` — iterable of `EventPatchImpactInput` dataclass instances, each specifying `lead_day`, `member_id`, `rp`, `depth_raster` path, and `extra` keyword args for the aggregator
- `worldpop_tif` — `Path` to the WorldPop GeoTIFF
- `depth_threshold_m` — depth threshold in metres

**Outputs:**
- `members` (List[int]) — sorted list of member IDs encountered
- `leads` (List[int]) — sorted list of lead days encountered
- `ImpactCube` (nested dict)

---

#### `_compute_prob_exceed(cube, thresholds, members) -> Dict[str, Dict[int, Dict[int, float]]]`

**Purpose:** Computes, for each unit and lead day, the fraction of ensemble members whose impact exceeds the OEP threshold for each return period.

**Inputs:**
- `cube` — `ImpactCube`
- `thresholds` — `{unit_id: {rp: threshold_people}}` from `extract()`
- `members` — iterable of member IDs (used to compute the denominator)

**Output:** `{unit_id: {lead_day: {rp: exceedance_probability}}}` where probability is in `[0.0, 1.0]`

**Note:** Only units present in both `cube` and `thresholds` appear in the output. Units that were filtered out by `oep_min` during extract will be absent here.

---

#### `_apply_tier_rules(prob_exceed, thresholds, unit_metadata, impact_cube, members, decision) -> List[UnitDecision]`

**Purpose:** Evaluates all tier rules against the exceedance probability cube and returns a `UnitDecision` for each qualifying unit.

**Inputs:**
- `prob_exceed` — output of `_compute_prob_exceed()`
- `thresholds`, `unit_metadata` — from `extract()`
- `impact_cube` — `ImpactCube`, used to compute `impact_population_at_fire` (ensemble mean at fire lead)
- `members` — list of member IDs for ensemble mean calculation
- `decision` — `DecisionSettings` (contains `rules`, `persist_days`, `min_lead`)

**Output:** `List[UnitDecision]`, where each object has:
- `unit_id`, `level`, `name`, `pcode`
- `tiers`: list of `TierDecision` objects, one per rule, with fields `tier`, `rp`, `p_threshold`, `fired` (bool), `fire_lead` (int or None), `probability_at_fire`, `impact_population_threshold`, `impact_population_at_fire`

---

#### `_find_latest_persistent_lead(firing_leads, min_lead, persist_days) -> Optional[int]`

**Purpose:** Given a set of lead days at which a tier's probability threshold was exceeded, returns the latest lead day that satisfies both the persistence window and minimum-lead constraints.

**Inputs:**
- `firing_leads` — iterable of integer lead days where the threshold was met
- `min_lead` — minimum lead day to consider (from `decision.min_lead`)
- `persist_days` — required length of contiguous window (from `decision.persist_days`)

**Output:** integer lead day (the highest qualifying one), or `None` if no lead satisfies the constraints

**Logic:** For `persist_days > 1`, the function checks whether any contiguous window of `persist_days` consecutive integers, all present in `firing_leads`, contains a lead day ≥ `min_lead`. The latest such lead day is returned. For `persist_days ≤ 1`, it simply returns the maximum qualifying lead.

---

### 6.3 Save stage (`etl/save.py`)

The save stage is purely about serialisation and visual output. It takes the structured decision objects from the forecast stage and writes them to disk in formats suitable for operational use. No scientific computation happens here. Failures in this stage are almost always caused by missing configuration (output directory template), missing geometry files (ADM3 GeoJSON), or geopandas/matplotlib rendering errors.

---

#### `save(run_spec, issue_date, basin_forecasts) -> Dict[str, Any]`

**Purpose:** Top-level entry point for the save stage. Orchestrates metadata preparation, CSV writing, decision summary writing, and map generation.

**Inputs:**
- `run_spec` — `PipelineRunSpec`; `run_spec.output` must be non-None with a valid `output_dir_template`
- `issue_date` — `datetime.date`
- `basin_forecasts` — list of dicts, each the return value of `forecast()` for one basin

**Outputs — returned dictionary keys:**
- `basin_results` (List[BasinRunOutput]) — structured results for all basins
- `main_output_file` (Path) — path to the written CSV file
- `decision_summary_file` (Path or None) — path to `decision.txt`, or `None` if no tiers fired
- `map_files` (List[Path]) — list of paths to all written PNG files

---

#### `_prepare_trigger_decision_metadata(run_spec, issue_date, basin_forecasts) -> List[BasinRunOutput]`

**Purpose:** Wraps each basin's forecast output in a `BasinRunOutput` dataclass that also carries the run-level metadata (tier rule configuration, OEP settings, impacts source). This provides a self-contained record per run that can be inspected independently of the run spec.

**Inputs:** same as `save()` minus the output concern

**Output:** `List[BasinRunOutput]`, each with fields `basin_name`, `issue_date`, `forecast_paths`, `units` (List[UnitDecision]), `metadata` (dict with `rule_tiers`, `persist_days`, `min_lead`, `oep_min`, `oep_source`, `impacts_source`)

---

#### `_create_save_output_context(run_spec, issue_date) -> Dict[str, Any]`

**Purpose:** Resolves the output directory path from the `output_dir_template`, creates it if necessary, and stamps the current UTC time for use in file names.

**Inputs:** `run_spec.output.output_dir_template` (e.g. `"data/gold/trigger_decisions/{date}"`), `issue_date`

**Output:** dict with keys `output_dir` (Path) and `timestamp` (str, format `YYYYMMDDTHHMMSSZ`)

**Raises:** `ValueError` if `run_spec.output` is None

---

#### `_prepare_trigger_decision_records(basin_results) -> pd.DataFrame`

**Purpose:** Flattens the nested `BasinRunOutput → UnitDecision → TierDecision` hierarchy into a flat DataFrame suitable for CSV export.

**Input:** `List[BasinRunOutput]`

**Output:** `pd.DataFrame` with columns `issue_date`, `basin_name`, `level`, `name`, `pcode`, `tier`, `rp`, `p_threshold`, `fired`, `fire_lead`, `probability_at_fire`, `impact_population_threshold`, `impact_population_at_fire`

---

#### `_save_trigger_decisions(trigger_df, output_dir, issue_date, timestamp) -> Path`

**Purpose:** Writes the trigger decisions DataFrame to a timestamped CSV file in the output directory.

**Output:** `Path` to the written CSV file, named `trigger_decisions_<date>_<timestamp>.csv`

---

#### `_prepare_decision_summary(basin_results) -> Dict[str, Any] | None`

**Purpose:** Counts total fired tier decisions across all basins and units. If any fired, returns a payload for `decision.txt`. If none fired, returns `None` (causing `decision.txt` to be skipped).

**Input:** `List[BasinRunOutput]`

**Output:** `{"file_name": "decision.txt", "text": "triggered=True\n", "triggered": True}`, or `None`

---

#### `_prepare_trigger_decisions_for_plotting(trigger_df) -> pd.DataFrame`

**Purpose:** Filters the trigger DataFrame to only activated ADM3 rows that have a valid `fire_lead`, then selects the single highest-ranked tier per (basin, lead, pcode) group. The result is the minimal set of rows needed to drive map rendering.

**Input:** `pd.DataFrame` from `_prepare_trigger_decision_records()`

**Output:** filtered `pd.DataFrame` with a `tier_rank` column added, or empty DataFrame if no ADM3 activated rows exist

**Tier ranking:** T1 → rank 1, T2 → rank 2, T3 → rank 3; `.groupby().last()` after sort selects the highest rank

---

#### `_plot_activated_areas(run_spec, trigger_df, fired_df) -> List[tuple]`

**Purpose:** For each (basin, fire_lead) group in `fired_df`, creates a matplotlib figure showing all basin ADM3 units as grey outlines, with activated units filled by their tier colour.

**Inputs:**
- `run_spec.detection.adm3_geojson` — path to ADM3 boundaries
- `trigger_df` — full CSV DataFrame (used to get all basin units for the background layer)
- `fired_df` — filtered DataFrame from `_prepare_trigger_decisions_for_plotting()`

**Output:** `List[tuple(fig, filename)]` where `filename` is `<basin>_lead<lead>_activated_map.png`

---

#### `_plot_population_exposed(run_spec, trigger_df, fired_df) -> List[tuple]`

**Purpose:** Same spatial structure as `_plot_activated_areas()`, but colours activated units by the `impact_population_at_fire` value using the `YlOrRd` continuous colourmap.

**Output:** `List[tuple(fig, filename)]` where `filename` is `<basin>_lead<lead>_population_exposed_map.png`

---

#### `_save_maps(map_plots, output_dir) -> List[Path]`

**Purpose:** Writes each matplotlib figure to the `maps/` subdirectory of the output directory at 150 DPI, then closes the figure to release memory.

**Input:** `List[tuple(fig, filename)]` — combined output of `_plot_activated_areas()` and `_plot_population_exposed()`

**Output:** `List[Path]` — paths to all written PNG files

---

## 7. Maintenance, Logging, and Debugging

### 7.1 Logging system

The logging system is defined in `src/river_flood_monitoring/logging.py`. It uses Python's standard `logging` module with a custom formatter that adds derived context fields. Understanding the log format is the fastest way to locate failures: the `subfunc` field tells you exactly which function and line raised the message, which eliminates the need for stack-trace reading in most cases.

Log line format:
```
2026-06-16 17:02:19 | INFO     | save       | _save_trigger_decisions:172  | CSV output written: ...
```

Fields:
- `timestamp` — local time at log emission (`%Y-%m-%d %H:%M:%S`)
- `level` — `DEBUG`, `INFO`, `WARNING`, `ERROR`, or `CRITICAL`
- `major` — last component of the module name (e.g. `pipeline`, `forecast`, `save`)
- `subfunc:line` — function name and line number where the log call was made
- `message` — free-text log message

Log files are created once per run in `pipeline.py` via `setup_pipeline_file_log()`. The root logger (`river_flood_monitoring`) receives both a console handler (INFO and above) and a file handler (DEBUG and above). All sub-module loggers (`river_flood_monitoring.etl.forecast`, etc.) inherit from this root logger, so all messages appear in the same file.

Per-run log file location:
- `logs/<yyyy>/<mm>/<run_name>_<YYYYMMDDTHHMMSS>.txt`

Example: `logs/2026/06/daily_flood_monitoring_20260616T170219.txt`

### 7.2 Exit codes and CLI error handling

The CLI in `cli.py` wraps the entire pipeline in a `try/except` block and returns integer exit codes. The `logger.exception()` call in the except block logs the full Python traceback to both console and file, which means that even when running in an automated environment with captured stdout, the full stack trace will always appear in the log file.

Exit codes:
- `0` — run completed without exception
- `1` — any exception was raised (date parse error, basin validation error, or stage runtime failure)

### 7.3 Step-by-step debug workflow

The most effective debugging strategy for this workflow is to identify which stage failed by scanning the log, then replay only that stage in isolation. This narrows the problem space considerably and avoids repeating expensive spatial computations.

Step 1 — Find the right log file:
```bash
ls -lt logs/$(date +%Y)/$(date +%m)/
```

Step 2 — Scan for the first failure signal:
```bash
grep -E "ERROR|CRITICAL|Traceback" logs/2026/06/daily_flood_monitoring_<timestamp>.txt
```

Step 3 — Read context around the failure using the `subfunc:line` field:
- note the function name and line number
- open the corresponding source file and read the surrounding lines
- look at the INFO messages immediately before the error to understand what data state was reached

Step 4 — Identify which stage failed:
- `pipeline` or `extract` major → run `--extract` in isolation
- `forecast` major → run `--forecast` in isolation (reuses cached extract artifact)
- `save` major → run `--save` in isolation

Step 5 — Inspect cached artifacts if needed. The extract and forecast artifacts are human-readable JSON files in `data/etl_step_cache/<run_name>/<issue_date>/`. Checking them directly can reveal data issues (e.g. empty `forecast_paths`, zero qualifying units) without running any code.

Step 6 — Fix, validate, and re-run:
- after fixing, replay the affected stage with `--date <date>`
- confirm the fix with `grep "complete" logs/.../<new_log_file>.txt`

### 7.4 Common failure patterns

The following table maps error messages or symptoms to likely causes and resolution steps.

"Forecast file(s) not found: data/bronze/glofas/..."
- Cause: `ingest.forecast_path_template` does not match the actual filename pattern, or files are missing for the issue date
- Resolution: check the template in the run spec against an actual filename; verify the date pattern expands correctly

"Run spec must define inputs.oep_json"
- Cause: `inputs` block is missing or empty in the YAML run spec
- Resolution: check the run spec for missing or misindented `inputs:` section

"EVT parameters file is missing required columns after normalization: ['u', ...]"
- Cause: the Parquet file has different column names than expected
- Resolution: inspect the Parquet with `pd.read_parquet(...).columns`; add column renaming if needed

"Cannot detect ADM3 PCODE column in ADM3 GeoJSON"
- Cause: `inputs.adm3_unit_column` does not match any column in the GeoJSON
- Resolution: inspect `geopandas.read_file(...).columns` and update `adm3_unit_column` in the run spec

"Extract artifact not found for basin 'cagayan': ... Run with --extract first."
- Cause: running `--forecast` or `--save` without a prior `--extract` run for the same date
- Resolution: run `--extract` first, then replay the downstream step

Empty CSV / no map output, no error in log:
- Cause: all units filtered out by `oep_min`, or no ensemble members produced qualifying patches
- Resolution: check the "OEP thresholds loaded: N qualifying units" log line; check "flood members" counts per lead day

### 7.5 Operational health monitoring recommendations

Beyond individual run debugging, a maintainer responsible for daily operations should establish basic health monitoring to detect silent failures early — for example, runs that complete without error but produce no outputs because of upstream data feed issues.

Suggested monitoring checks:
- Confirm a new log file is created each day and contains the `"run_daily_monitoring_etl complete"` line
- Verify the trigger CSV in `data/gold/trigger_decisions/<date>/` grows in row count when conditions are broadly similar to prior days
- Alert if the CSV exists but `decision.txt` and map files are repeatedly absent across multiple run dates (may indicate upstream data feed degradation)
- Track total runtime duration per run; a sudden increase may indicate raster I/O bottlenecks or upstream file size changes
- Review the "qualifying patches" count per lead day in the log; zero patches across all leads warrants investigation of the forecast and EVT data

---

## 8. Additional Recommended Topics for Maintainers

This section identifies gaps and improvements that would meaningfully reduce the maintenance burden or risk of operational failures. These are not immediate bugs, but they represent technical debt that a maintainer should be aware of when planning code changes or preparing for production deployments.

1. **Resolve the discharge multiplier before production**
   `forecast.py` currently applies `q_vals = q_vals * 10` at two places during flood detection. This was introduced as a temporary adjustment to produce triggers during development with real forecast data. It must be removed before the workflow is used for real operational decisions, as it artificially inflates return period estimates and will produce false activations.

2. **Resolve the no-trigger decision.txt inconsistency**
   `save.py` currently skips writing `decision.txt` when no tiers fire, while `tests/test_save_decision.py` expects the file to contain `triggered=False`. A maintainer adding new tests or modifying the save stage should pick one behaviour and make the code and tests agree.

3. **Data contracts and schema versioning**
   The OEP JSON and EVT Parquet schemas are effectively implicit contracts between upstream calibration workflows and this ETL. Adding formal JSON Schema validation for the OEP file and column-presence checks for the Parquet (beyond the current normalisation) would make schema drift failures immediately obvious rather than producing silent wrong results.

4. **Regression testing with synthetic data**
   The current test suite covers rule logic and the save stage in isolation, but there are no end-to-end integration tests with synthetic spatial inputs. Creating a small synthetic dataset (a few GloFAS cells, a small JRC tile, a minimal WorldPop raster, and a stub OEP JSON) would allow the full pipeline to be exercised in CI without requiring the large real data files.

5. **Performance and memory tuning**
   The forecast stage processes up to 51 ensemble members × N lead days × M patches, each requiring a JRC tile merge and a raster overlay. For large basins, this can be slow. Profiling the JRC merge step (`rasterio.merge`) and the WorldPop reprojection step is a good starting point. If runtime becomes a concern, parallelising the per-member patch rendering loop (currently sequential) would be the highest-value optimisation.

6. **Structured logging and run health KPIs**
   The current log format is human-readable but not easily machine-parseable. Emitting a structured JSON summary at the end of each run (with fields such as `n_members`, `n_units_evaluated`, `n_tiers_fired`, `stage_durations_sec`) would enable automated monitoring dashboards without log parsing.

7. **Release and change management discipline**
   Changes to tier rule thresholds (`p_thr`, `rp`, `persist_days`) directly affect operational outputs. A changelog entry and versioned run-spec file for each such change (e.g. `daily_monitoring_etl_v2.yaml`) would allow historical runs to be replayed with the same configuration that was active at the time, which is important for post-event analysis.

---

*Prepared for maintainers with Python and Linux background, with emphasis on failure diagnosis and safe operational debugging.*
