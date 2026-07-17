# data_update

Standalone scripts for downloading and refreshing static reference data used by the flood workflow.

---

## download_jrc_flood_tiles.py

Downloads JRC CEMS-GloFAS flood hazard depth GeoTIFFs for a given bounding box and set of return periods.

This script extracts the tile-download logic from the `og_notebooks/02_HazardOnly_Workflow.ipynb` notebook (Section 3) into a standalone, rerunnable script — decoupled from the notebook's calibration run directories.

### What it does

1. Downloads (and caches) the JRC tile index (`tile_extents.geojson`) from the JRC FTP mirror.
2. Selects the tiles that intersect the provided bounding box.
3. Downloads `_depth.tif` (or `_depth_reclass.tif`) tiles for each requested return period into `RP{n}/` subdirectories under the output directory.
4. Skips tiles that are already present and non-empty (safe to re-run).

### JRC data source

`https://jeodpp.jrc.ec.europa.eu/ftp/jrc-opendata/CEMS-GLOFAS/flood_hazard/`

JRC's available return periods: **10, 20, 50, 75, 100, 200, 500** years.

### Usage

```powershell
# Download all return periods for a bounding box (lon_min lat_min lon_max lat_max)
uv run python data_update/download_jrc_flood_tiles.py `
    --bbox 117.0 14.5 123.5 19.5

# Restrict to specific return periods
uv run python data_update/download_jrc_flood_tiles.py `
    --bbox 117.0 14.5 123.5 19.5 `
    --return-periods 10 100 500

# Use reclassified depth maps instead of raw depth
uv run python data_update/download_jrc_flood_tiles.py `
    --bbox 117.0 14.5 123.5 19.5 `
    --reclassified

# Write tiles to a custom directory
uv run python data_update/download_jrc_flood_tiles.py `
    --bbox 117.0 14.5 123.5 19.5 `
    --output-dir data/bronze/jrc_flood_maps
```

### Arguments

| Argument | Required | Default | Description |
|---|---|---|---|
| `--bbox LON_MIN LAT_MIN LON_MAX LAT_MAX` | Yes | — | Bounding box in WGS-84 degrees |
| `--return-periods RP [RP ...]` | No | all 7 | Return periods to download (years) |
| `--output-dir PATH` | No | `data/bronze/jrc_flood_maps` | Root directory for cached tiles |
| `--reclassified` | No | false | Download `_depth_reclass.tif` instead of `_depth.tif` |

### Output structure

```
data/bronze/jrc_flood_maps/
    tile_extents.geojson        ← cached tile index
    RP10/
        ID231_N16_E120_RP10_depth.tif
        ...
    RP100/
        ...
    RP500/
        ...
```

The output directory layout matches what `02_HazardOnly_Workflow.ipynb` Section 4 expects when merging tiles into `flood-maps_intermediate.nc`.

### Dependencies

All dependencies are declared in `pyproject.toml`. The only package added specifically for this script (not previously in the project) is `requests>=2.28`.
