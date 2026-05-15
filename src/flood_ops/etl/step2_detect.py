"""Step 2 — Detect: spatial flood event detection from a GloFAS ensemble.

Implements the NB07 detection algorithm (cells 3–10):

1. Load EVT1 GPD fits from ``evt_pot_calibration.parquet`` (NB01 output).
2. For each ensemble member × lead time, extract discharge from the GRIB,
   compute per-cell return periods, run 8-neighbour connected-component
   labelling, and filter out patches smaller than ``A_MIN_KM2``.
3. For flood-triggering members, run the full JRC+WorldPop impact pipeline
   (NB04-style) to compute people affected per admin unit per RP.
4. Return an ``ImpactCube`` compatible with ``step3_impact`` / ``step4_evaluate``.

References
----------
NB07 ``07_Trigger_Validation_Reforecast.ipynb`` — cells 3–10 (reference impl.)
"""

from __future__ import annotations

import json
import re
import warnings
from contextlib import ExitStack
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING, Dict, List, Optional, Tuple

from flood_ops.logging import get_logger
from .step3_impact import ImpactCube

if TYPE_CHECKING:
    import numpy as np
    import pandas as pd
    from flood_ops.etl.run_spec import DetectionSettings

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_RP_CAP = 500.0


# ---------------------------------------------------------------------------
# EVT1 GPD helpers  (extracted from NB07 Cell 3)
# ---------------------------------------------------------------------------

def _gpd_exceedance_rate(
    q, u, sigma, xi, lam,
):
    """POT-Poisson-GPD exceedance rate (events/year) for discharge q."""
    import numpy as np
    q, u, sigma, xi, lam = np.broadcast_arrays(
        np.asarray(q, float), np.asarray(u, float),
        np.asarray(sigma, float), np.asarray(xi, float), np.asarray(lam, float),
    )
    y = np.maximum((q - u) / sigma, 0.0)
    near0 = np.isclose(xi, 0.0)
    surv = np.empty_like(y)
    surv[near0] = np.exp(-y[near0])
    surv[~near0] = np.power(1.0 + xi[~near0] * y[~near0], -1.0 / xi[~near0])
    return np.clip(lam * surv, 1e-12, None)


def discharge_to_return_period(q, u, sigma, xi, lam, rp_cap: float = _RP_CAP):
    """Convert discharge to return period (years) via EVT1 GPD."""
    import numpy as np
    rate = _gpd_exceedance_rate(q=q, u=u, sigma=sigma, xi=xi, lam=lam)
    return np.clip(1.0 / rate, 1.0, rp_cap)


# ---------------------------------------------------------------------------
# Spatial helpers  (extracted from NB07 Cell 3)
# ---------------------------------------------------------------------------

def _parse_cell_coords(cell_id: str) -> Tuple[float, float]:
    """Extract (lat, lon) from cell_id = 'CELL__lat_XX.XXXX__lon_YY.YYYY'."""
    m = re.search(r"lat_([-\d.]+)__lon_([-\d.]+)", str(cell_id))
    if not m:
        raise ValueError(f"Cannot parse lat/lon from cell_id={cell_id!r}")
    return float(m.group(1).rstrip(".")), float(m.group(2).rstrip("."))


def _earth_cell_area_km2(lat_deg, dlat_deg: float, dlon_deg: float):
    """Approximate area of a lat-lon grid cell at each latitude (km²)."""
    import numpy as np
    R = 6371.0
    lat_rad = np.deg2rad(lat_deg)
    return R * R * np.deg2rad(dlat_deg) * np.deg2rad(dlon_deg) * np.cos(lat_rad)


def _build_support_grid(evt_params) -> Tuple:
    """Build the 2-D support grid for connected-component detection."""
    import numpy as np
    ep = evt_params.set_index("cell_id")
    lat_vals = np.sort(ep["lat"].unique())
    lon_vals = np.sort(ep["lon"].unique())
    dlat = float(np.median(np.abs(np.diff(lat_vals)))) if len(lat_vals) > 1 else 0.1
    dlon = float(np.median(np.abs(np.diff(lon_vals)))) if len(lon_vals) > 1 else 0.1
    lat_to_i = {float(v): i for i, v in enumerate(lat_vals)}
    lon_to_j = {float(v): j for j, v in enumerate(lon_vals)}
    nlat, nlon = len(lat_vals), len(lon_vals)
    grid_index = -np.ones((nlat, nlon), dtype=int)
    cell_ids = ep.index.tolist()
    for k, cid in enumerate(cell_ids):
        la = float(ep.loc[cid, "lat"])
        lo = float(ep.loc[cid, "lon"])
        grid_index[lat_to_i[la], lon_to_j[lo]] = k
    area_lat = _earth_cell_area_km2(lat_vals, dlat, dlon)
    area_grid = area_lat[:, None] * np.ones((1, nlon))
    return lat_vals, lon_vals, grid_index, area_grid, lat_vals.copy(), lon_vals.copy()


# ---------------------------------------------------------------------------
# GRIB reading utilities  (extracted from NB07 Cell 3 section [C])
# ---------------------------------------------------------------------------

def _nearest_index_1d(arr_1d, targets):
    import numpy as np
    arr = np.asarray(arr_1d, float)
    t = np.asarray(targets, float)
    dif = np.diff(arr)
    is_asc = bool(np.all(dif >= 0))
    is_desc = bool(np.all(dif <= 0))
    if not (is_asc or is_desc):
        return np.array([int(np.argmin(np.abs(arr - x))) for x in t])
    rev = False
    arr_use = arr
    if is_desc:
        arr_use = arr[::-1]
        rev = True
    idx = np.searchsorted(arr_use, t, side="left")
    idx = np.clip(idx, 1, len(arr_use) - 1)
    left = arr_use[idx - 1]
    right = arr_use[idx]
    idx_final = np.where(np.abs(right - t) < np.abs(t - left), idx, idx - 1).astype(int)
    if rev:
        idx_final = (len(arr) - 1) - idx_final
    return idx_final


def _dt_from_grib(data_date: int, data_time: int):
    import pandas as pd
    return pd.to_datetime(f"{int(data_date):08d}{int(data_time):04d}", format="%Y%m%d%H%M")


def _build_msg_index(grbs, shortname: str) -> dict:
    """Index all GRIB messages for *shortname*."""
    import pandas as pd
    msg_index: dict = {}
    vt_lookup: dict = {}
    inits: set = set()
    steps: set = set()
    members: set = set()
    tmpl = None
    grbs.rewind()
    for i, g in enumerate(grbs, 1):
        try:
            if g.shortName != shortname:
                continue
        except Exception:
            continue
        try:
            dd, tt, es, pn = int(g.dataDate), int(g.dataTime), int(g.endStep), int(g.perturbationNumber)
        except Exception:
            continue
        msg_index[(dd, tt, es, pn)] = i
        inits.add((dd, tt))
        steps.add(es)
        members.add(pn)
        if tmpl is None:
            tmpl = i
        init_ts = pd.Timestamp(year=dd // 10000, month=(dd % 10000) // 100,
                               day=dd % 100, hour=tt // 100, minute=tt % 100)
        vd = init_ts + pd.Timedelta(hours=es)
        vt_lookup[(vd, pn, init_ts)] = (dd, tt, es, pn)
        vt_lookup.setdefault((vd, pn), ((dd, tt, es, pn), init_ts))
    if not msg_index:
        raise RuntimeError(f"No GRIB messages found for shortName='{shortname}'")
    return {
        "msg_index": msg_index, "inits": sorted(inits), "steps": sorted(steps),
        "members": sorted(members), "tmpl": tmpl, "vt_lookup": vt_lookup,
    }


def _read_grib_snapshot(
    grbs,
    msg_index: dict,
    vt_lookup: dict,
    valid_date: pd.Timestamp,
    member: int,
    init_dt: Optional[pd.Timestamp],
    cell_lat_idx: np.ndarray,
    cell_lon_idx: np.ndarray,
) -> Optional[np.ndarray]:
    """Read discharge values for *member* at *valid_date*, returns array per cell."""
    key_data = vt_lookup.get((valid_date, member, init_dt)) if init_dt else None
    if key_data is None:
        hit = vt_lookup.get((valid_date, member))
        if hit is None:
            return None
        key_data = hit[0]
    try:
        msg_num = msg_index.get(key_data)
        if msg_num is None:
            return None
        msg = grbs.message(msg_num)
        data = msg.values
        return data[cell_lat_idx, cell_lon_idx].astype(float)
    except Exception as exc:
        logger.debug("GRIB read error for key %s: %s", key_data, exc)
        return None


# ---------------------------------------------------------------------------
# Connected-component flood detection  (NB07 Cell 3 detect_flood_members)
# ---------------------------------------------------------------------------

def _detect_flood_members_for_lead(
    grbs,
    grib_index: dict,
    lead_window,
    basin_cells: List[str],
    evt_params,
    grid_index,
    area_grid_km2,
    cell_lat_idx,
    cell_lon_idx,
    t0_years: float,
    a_min_km2: float,
    connectivity: int,
    init_dt,
) -> List[int]:
    """Phase 1: return list of member IDs that trigger the flood detection rule."""
    import numpy as np
    from scipy.ndimage import label, generate_binary_structure

    ep = evt_params.set_index("cell_id")
    cells = [c for c in basin_cells if c in ep.index]
    if not cells:
        return []

    u = ep.loc[cells, "u"].values[None, :]
    sigma = ep.loc[cells, "sigma"].values[None, :]
    xi = ep.loc[cells, "xi"].values[None, :]
    lam = ep.loc[cells, "lam"].values[None, :]

    struct = generate_binary_structure(2, connectivity)
    flood_members: List[int] = []

    for member in grib_index["members"]:
        triggered = False
        for vdate in lead_window:
            q_vals = _read_grib_snapshot(
                grbs, grib_index["msg_index"], grib_index["vt_lookup"],
                vdate, member, init_dt, cell_lat_idx, cell_lon_idx,
            )
            if q_vals is None:
                continue
            rp = discharge_to_return_period(
                q_vals[None, :], u, sigma, xi, lam
            ).ravel()
            active_cells = rp >= t0_years
            if not active_cells.any():
                continue
            g = np.zeros(grid_index.shape, dtype=bool)
            for k, cid in enumerate(cells):
                if active_cells[k]:
                    positions = np.argwhere(grid_index == k)
                    for pos in positions:
                        g[pos[0], pos[1]] = True
            if not g.any():
                continue
            labs, nlab = label(g, structure=struct)
            if nlab == 0:
                continue
            areas = np.bincount(labs.ravel(), weights=area_grid_km2.ravel())
            if len(areas) > 1 and np.max(areas[1:]) >= a_min_km2:
                triggered = True
                break
        if triggered:
            flood_members.append(member)
    return flood_members


# ---------------------------------------------------------------------------
# Spatial resources loader
# ---------------------------------------------------------------------------

def _load_spatial_resources(settings: "DetectionSettings") -> dict:
    """Load JRC flood maps, WorldPop raster, and admin rasters.

    Returns a dict with keys: jrc_maps, pop_grid, admin_id_raster,
    admin_ids, admin_id_to_name, rp_to_files.
    Raises FileNotFoundError when required paths are missing.
    """
    import rasterio
    import xarray as xr
    from pathlib import Path

    jrc_root = Path(settings.jrc_root)
    worldpop_tif = Path(settings.worldpop_tif)
    adm3_geojson = Path(settings.adm3_geojson)

    if not jrc_root.exists():
        raise FileNotFoundError(f"JRC root not found: {jrc_root}")
    if not worldpop_tif.exists():
        raise FileNotFoundError(f"WorldPop TIF not found: {worldpop_tif}")
    if not adm3_geojson.exists():
        raise FileNotFoundError(f"ADM3 GeoJSON not found: {adm3_geojson}")

    # Scan JRC directory
    jrc_tifs = sorted(jrc_root.rglob("*.tif")) + sorted(jrc_root.rglob("*.tiff"))
    rp_to_files: Dict[int, List[Path]] = {}
    _rp_re = re.compile(r"(?:rp|RP|return_period|RP_)(\d+)", re.IGNORECASE)
    for tif in jrc_tifs:
        m = _rp_re.search(tif.stem)
        if m:
            rp = int(m.group(1))
            rp_to_files.setdefault(rp, []).append(tif)

    # Load WorldPop
    with rasterio.open(worldpop_tif) as src:
        pop_grid = src.read(1).astype(np.float32)
        pop_transform = src.transform
        pop_nodata = src.nodata
    if pop_nodata is not None:
        pop_grid[pop_grid == pop_nodata] = np.nan

    # Load admin rasters from GeoJSON (rasterise to WorldPop grid)
    import geopandas as gpd
    from rasterio.features import rasterize
    from rasterio.transform import from_bounds

    adm3_gdf = gpd.read_file(adm3_geojson).to_crs("EPSG:4326")
    # Build integer ID → name mapping
    admin_id_to_name: Dict[int, str] = {}
    name_col = None
    for col in adm3_gdf.columns:
        if "adm3" in col.lower() and "name" in col.lower():
            name_col = col
            break
    if name_col is None:
        # Fall back to first string column after geometry
        for col in adm3_gdf.columns:
            if col != "geometry" and adm3_gdf[col].dtype == object:
                name_col = col
                break
    if name_col is None:
        raise ValueError("Cannot detect admin name column in ADM3 GeoJSON")

    shapes = []
    for idx_row, row in enumerate(adm3_gdf.itertuples(), start=1):
        admin_id_to_name[idx_row] = str(getattr(row, name_col))
        shapes.append((row.geometry, idx_row))

    with rasterio.open(worldpop_tif) as src:
        admin_id_raster = rasterize(
            shapes=shapes,
            out_shape=src.shape,
            transform=src.transform,
            fill=0,
            dtype="int32",
        )
    admin_ids = np.array(sorted(admin_id_to_name.keys()), dtype="int32")

    return {
        "rp_to_files": rp_to_files,
        "pop_grid": pop_grid,
        "admin_id_raster": admin_id_raster,
        "admin_ids": admin_ids,
        "admin_id_to_name": admin_id_to_name,
        "adm3_gdf": adm3_gdf,
        "name_col": name_col,
    }


# ---------------------------------------------------------------------------
# Impact pipeline for one discharge snapshot  (NB07 run_full_impact_pipeline)
# ---------------------------------------------------------------------------

def _compute_impact_snapshot(
    discharge_per_cell,
    evt_params,
    spatial: dict,
    flood_detect_rps: List[int],
    depth_threshold_m: float,
    bbox: Tuple[float, float, float, float],
    event_id: str = "detect",
) -> Dict[str, float]:
    """Return {unit_name: people_affected} for one member × valid_date snapshot."""
    import numpy as np
    import pandas as pd
    import xarray as xr
    import rasterio
    from rasterio.merge import merge
    from shapely.geometry import box as sbox

    ep = evt_params.set_index("cell_id")
    cells = [c for c in discharge_per_cell.index if c in ep.index]
    if not cells:
        return {}

    q = discharge_per_cell[cells].fillna(0.0).values[None, :]
    rp_vals = discharge_to_return_period(
        q,
        ep.loc[cells, "u"].values[None, :],
        ep.loc[cells, "sigma"].values[None, :],
        ep.loc[cells, "xi"].values[None, :],
        ep.loc[cells, "lam"].values[None, :],
    ).ravel()

    # Early exit: skip JRC regriddding if no cell reaches RP >= 2yr
    if np.nanmax(rp_vals) < 2.0:
        result: Dict[str, float] = {}
        for aid, aname in spatial["admin_id_to_name"].items():
            result[f"ADM3::{aname}"] = 0.0
        result["WATERSHED::TOTAL"] = 0.0
        return result

    # Build 2-D RP grid
    rp_df = pd.DataFrame({
        "lat": ep.loc[cells, "lat"].values,
        "lon": ep.loc[cells, "lon"].values,
        "rp": rp_vals,
    })
    lat_vals = np.sort(rp_df["lat"].unique())
    lon_vals = np.sort(rp_df["lon"].unique())
    grid = np.full((len(lat_vals), len(lon_vals)), np.nan, dtype="float32")
    lat_idx = {v: i for i, v in enumerate(lat_vals)}
    lon_idx = {v: j for j, v in enumerate(lon_vals)}
    for _, row in rp_df.iterrows():
        grid[lat_idx[row["lat"]], lon_idx[row["lon"]]] = row["rp"]

    rp_grid_da = xr.DataArray(
        grid,
        coords={"latitude": lat_vals, "longitude": lon_vals},
        dims=("latitude", "longitude"),
        name="rp",
    )

    # Interpolate JRC depth maps at grid RPs
    rp_to_files = spatial["rp_to_files"]
    minx, miny, maxx, maxy = bbox
    bbox_geom = sbox(minx, miny, maxx, maxy)

    available_rps = sorted(rp for rp in rp_to_files if rp_to_files[rp])
    if not available_rps:
        return {}

    # Load JRC tiles for each RP within bbox
    depth_arrays: Dict[int, np.ndarray] = {}
    ref_transform = None
    ref_shape: Optional[Tuple[int, int]] = None
    for rp in available_rps:
        tif_paths = [p for p in rp_to_files[rp]
                     if sbox(*rasterio.open(p).bounds).intersects(bbox_geom)]
        if not tif_paths:
            tif_paths = rp_to_files[rp]
        try:
            with ExitStack() as stack:
                srcs = [stack.enter_context(rasterio.open(p)) for p in tif_paths]
                mosaic, trans = merge(srcs, bounds=(minx, miny, maxx, maxy))
            arr = mosaic[0].astype(np.float32)
            nodata = rasterio.open(tif_paths[0]).nodata
            if nodata is not None:
                arr[arr == nodata] = np.nan
            if ref_transform is None:
                ref_transform = trans
                ref_shape = arr.shape
            depth_arrays[rp] = arr
        except Exception as exc:
            logger.debug("JRC load error RP%d: %s", rp, exc)

    if not depth_arrays:
        return {}

    h, w = ref_shape  # type: ignore[misc]
    # Interpolate depth at event RP using log-linear interpolation between available RPs
    rps_sorted = sorted(depth_arrays)
    event_rp = float(np.nanmax(rp_vals))
    if event_rp <= rps_sorted[0]:
        depth_np = depth_arrays[rps_sorted[0]].copy()
    elif event_rp >= rps_sorted[-1]:
        depth_np = depth_arrays[rps_sorted[-1]].copy()
    else:
        lo = max(r for r in rps_sorted if r <= event_rp)
        hi = min(r for r in rps_sorted if r >= event_rp)
        if lo == hi:
            depth_np = depth_arrays[lo].copy()
        else:
            t = (np.log(event_rp) - np.log(lo)) / (np.log(hi) - np.log(lo))
            d_lo = np.nan_to_num(depth_arrays[lo], nan=0.0)
            d_hi = np.nan_to_num(depth_arrays[hi], nan=0.0)
            depth_np = ((1 - t) * d_lo + t * d_hi).astype(np.float32)

    # Reproject WorldPop and admin rasters to depth grid if needed
    pop_grid = spatial["pop_grid"]
    admin_id_raster = spatial["admin_id_raster"]
    admin_id_to_name = spatial["admin_id_to_name"]

    # Use rasterio to reproject to depth grid bounds/resolution
    from rasterio.transform import from_bounds as _from_bounds
    from rasterio.warp import reproject as _reproject, Resampling

    depth_transform = ref_transform
    with rasterio.open(
        list(rp_to_files[available_rps[0]])[0]
    ) as _src_ref:
        src_crs = _src_ref.crs
        src_transform_wp = _src_ref.transform  # approximate, WorldPop may differ

    import rasterio
    from rasterio.crs import CRS as _CRS

    _epsg4326 = _CRS.from_epsg(4326)

    # Reproject pop grid to depth extent
    pop_reproj = np.zeros((h, w), dtype=np.float32)
    with rasterio.open(spatial.get("worldpop_tif", "")) if spatial.get("worldpop_tif") else \
            rasterio.MemoryFile() as _wp:
        pass  # fallback: we already have pop_grid in memory; use nearest reproject

    # Simplified approach: slice pop/admin to the bbox pixel region
    # (works when pop and JRC grids share EPSG:4326 with similar resolution)
    flooded = np.isfinite(depth_np) & (depth_np >= depth_threshold_m)
    if not flooded.any():
        result = {f"ADM3::{n}": 0.0 for n in admin_id_to_name.values()}
        result["WATERSHED::TOTAL"] = 0.0
        return result

    # Aggregate affected population per admin unit
    # Since pop_grid and admin rasters may have different resolutions,
    # we use the admin_id_raster shape and scale indices.
    ph, pw = pop_grid.shape
    ah, aw = admin_id_raster.shape
    # Map depth grid pixels to pop/admin grid pixels via bbox fractions
    flooded_flat = flooded.ravel()
    if ah == h and aw == w:
        admin_flat = admin_id_raster.ravel()
        pop_flat = pop_grid.ravel()
    else:
        # Nearest-neighbour resample admin and pop to depth grid size
        from scipy.ndimage import zoom
        zy = h / ah
        zx = w / aw
        admin_resampled = zoom(admin_id_raster.astype(float), (h / ah, w / aw), order=0).astype("int32")
        pop_resampled = zoom(np.nan_to_num(pop_grid, nan=0.0), (h / ph, w / pw), order=1).astype("float32")
        admin_flat = admin_resampled.ravel()
        pop_flat = pop_resampled.ravel()

    pop_flat = np.nan_to_num(np.asarray(pop_flat, float), nan=0.0)
    pop_flat = np.clip(pop_flat, 0.0, None)

    impact: Dict[int, float] = {}
    valid_mask = flooded_flat & (admin_flat > 0)
    for aid in admin_id_to_name:
        cell_mask = valid_mask & (admin_flat == aid)
        impact[aid] = float(pop_flat[cell_mask].sum())

    result = {}
    for aid, aname in admin_id_to_name.items():
        result[f"ADM3::{aname}"] = impact.get(aid, 0.0)
    result["WATERSHED::TOTAL"] = sum(impact.values())
    return result


# ---------------------------------------------------------------------------
# Main public function
# ---------------------------------------------------------------------------

def detect_flood_events(
    forecast_path: str,
    evt_params_path: Path,
    oep_json_path: Path,
    issue_date: date,
    basin_id: str,
    settings: "DetectionSettings",
    lead_days_list: Optional[List[int]] = None,
) -> Tuple[ImpactCube, List[int], List[int]]:
    """Run the NB07 spatial flood-event detection algorithm.

    Parameters
    ----------
    forecast_path:
        Path to the GloFAS ensemble GRIB file for *issue_date*.
    evt_params_path:
        Path to ``evt_pot_calibration.parquet`` (NB01 output).
    oep_json_path:
        Path to ``oep_curves_all_units.json`` (NB05 output) — used to
        read unit names for the impact cube.
    issue_date:
        Forecast initialisation date.
    basin_id:
        Basin identifier (for logging).
    settings:
        Detection hyper-parameters (t0_years, a_min_km2, etc.).
    lead_days_list:
        Lead times to evaluate (days). Defaults to 1–15.

    Returns
    -------
    (impact_cube, ensemble_members, lead_days)
        *impact_cube* is a nested dict compatible with
        ``step3_impact.ImpactCube``.
    """
    if lead_days_list is None:
        lead_days_list = list(range(1, 16))

    logger.info(
        "Step 2 detect_flood_events — basin='%s', issue_date=%s, "
        "leads=%s, t0_years=%.1f, a_min_km2=%.0f",
        basin_id, issue_date, lead_days_list,
        settings.t0_years, settings.a_min_km2,
    )

    # --- Load EVT1 parameters ------------------------------------------------
    evt_params_path = Path(evt_params_path)
    if not evt_params_path.exists():
        raise FileNotFoundError(f"EVT1 params not found: {evt_params_path}")

    import numpy as np
    import pandas as pd

    evt_raw = pd.read_parquet(evt_params_path)
    _col_map = {
        "virtual_gauge_id": "cell_id",
        "threshold_m3s": "u",
        "lambda_events_per_year": "lam",
        "gpd_xi": "xi",
        "gpd_scale_sigma": "sigma",
    }
    evt_params = evt_raw.rename(
        columns={k: v for k, v in _col_map.items() if k in evt_raw.columns}
    ).copy()
    if "lat" not in evt_params.columns or "lon" not in evt_params.columns:
        ll = evt_params["cell_id"].astype(str).apply(_parse_cell_coords)
        evt_params["lat"] = [v[0] for v in ll]
        evt_params["lon"] = [v[1] for v in ll]

    basin_cells = evt_params["cell_id"].astype(str).tolist()
    logger.info("EVT1 params loaded: %d cells for basin '%s'", len(basin_cells), basin_id)

    # --- Build support grid --------------------------------------------------
    lat_vals, lon_vals, grid_index, area_grid, lat1d, lon1d = _build_support_grid(evt_params)

    # --- Load spatial resources ----------------------------------------------
    spatial = _load_spatial_resources(settings)
    minx = float(lon_vals.min()) - 0.5
    maxx = float(lon_vals.max()) + 0.5
    miny = float(lat_vals.min()) - 0.5
    maxy = float(lat_vals.max()) + 0.5
    bbox = (minx, miny, maxx, maxy)
    spatial["worldpop_tif"] = settings.worldpop_tif

    # --- Read unit names from OEP JSON (to initialise ImpactCube) -----------
    import json as _json
    oep_raw = _json.loads(Path(oep_json_path).read_text(encoding="utf-8"))
    unit_names: List[str] = [r["unit"] for r in oep_raw.get("units", [])]

    # --- Open GRIB -----------------------------------------------------------
    try:
        import pygrib  # type: ignore
    except ImportError as exc:
        raise ImportError(
            "pygrib is required for step2 GRIB reading. "
            "Install it with: pip install pygrib"
        ) from exc

    grib_path = Path(forecast_path)
    if not grib_path.exists():
        raise FileNotFoundError(f"Forecast GRIB not found: {grib_path}")

    grbs = pygrib.open(str(grib_path))
    try:
        grib_index = _build_msg_index(grbs, settings.grib_shortname)
    except RuntimeError as exc:
        grbs.close()
        raise RuntimeError(
            f"Failed to index GRIB {grib_path}: {exc}"
        ) from exc

    # Pre-compute cell → GRIB grid index mapping
    tmpl_msg = grbs.message(grib_index["tmpl"])
    lats2d, lons2d = tmpl_msg.latlons()
    grib_lat1d = lats2d[:, 0]
    grib_lon1d = lons2d[0, :]
    cell_lats = np.array([_parse_cell_coords(c)[0] for c in basin_cells])
    cell_lons = np.array([_parse_cell_coords(c)[1] for c in basin_cells])
    cell_lat_idx = _nearest_index_1d(grib_lat1d, cell_lats)
    cell_lon_idx = _nearest_index_1d(grib_lon1d, cell_lons)

    init_dt = pd.Timestamp(issue_date)
    avail_inits = sorted(
        {_dt_from_grib(dd, tt) for dd, tt in grib_index["inits"]}
    )
    if avail_inits and init_dt not in avail_inits:
        nearest_init = min(avail_inits, key=lambda d: abs(d - init_dt))
        logger.warning(
            "init_dt %s not in GRIB; using nearest %s", init_dt.date(), nearest_init.date()
        )
        init_dt = nearest_init

    all_members: List[int] = grib_index["members"]
    impact_cube: ImpactCube = {}

    # --- Main loop: per lead time -------------------------------------------
    for lead_days in lead_days_list:
        lead_window = pd.date_range(
            init_dt + pd.Timedelta(days=1),
            init_dt + pd.Timedelta(days=lead_days),
            freq="D",
        )

        # Phase 1: detect flood members via connected-component rule
        flood_members = _detect_flood_members_for_lead(
            grbs=grbs,
            grib_index=grib_index,
            lead_window=lead_window,
            basin_cells=basin_cells,
            evt_params=evt_params,
            grid_index=grid_index,
            area_grid_km2=area_grid,
            cell_lat_idx=cell_lat_idx,
            cell_lon_idx=cell_lon_idx,
            t0_years=settings.t0_years,
            a_min_km2=settings.a_min_km2,
            connectivity=settings.cc_connectivity,
            init_dt=init_dt,
        )
        logger.info(
            "Lead %2dd: %d/%d flood members detected",
            lead_days, len(flood_members), len(all_members),
        )

        if not flood_members:
            # Fill cube with zeros for this lead (all members, all units, all RPs)
            for member in all_members:
                for unit in unit_names:
                    rp_dict = {rp: 0.0 for rp in settings.flood_detect_rps}
                    impact_cube.setdefault(unit, {}).setdefault(lead_days, {})[member] = rp_dict
            continue

        # Phase 2: full impact pipeline for flood members only
        for member in flood_members:
            q_snapshot: Optional[pd.Series] = None
            # Use the last (peak) valid date in the lead window for impact
            for vdate in reversed(lead_window):
                q_vals = _read_grib_snapshot(
                    grbs, grib_index["msg_index"], grib_index["vt_lookup"],
                    vdate, member, init_dt, cell_lat_idx, cell_lon_idx,
                )
                if q_vals is not None:
                    q_snapshot = pd.Series(q_vals, index=basin_cells)
                    break

            if q_snapshot is None:
                rp_dict = {rp: 0.0 for rp in settings.flood_detect_rps}
                for unit in unit_names:
                    impact_cube.setdefault(unit, {}).setdefault(lead_days, {})[member] = rp_dict
                continue

            # Compute impact at each RP
            rp_dict_by_unit: Dict[str, Dict[int, float]] = {}
            for rp in settings.flood_detect_rps:
                snap_impact = _compute_impact_snapshot(
                    discharge_per_cell=q_snapshot,
                    evt_params=evt_params,
                    spatial=spatial,
                    flood_detect_rps=[rp],
                    depth_threshold_m=settings.depth_threshold_m,
                    bbox=bbox,
                    event_id=f"{basin_id}_lead{lead_days:02d}_mbr{member:03d}_rp{rp}",
                )
                for unit, pop in snap_impact.items():
                    rp_dict_by_unit.setdefault(unit, {})[rp] = pop

            # Fill zero-impact members
            for unit in unit_names:
                cube_rp = rp_dict_by_unit.get(unit, {})
                full_rp = {rp: cube_rp.get(rp, 0.0) for rp in settings.flood_detect_rps}
                impact_cube.setdefault(unit, {}).setdefault(lead_days, {})[member] = full_rp

        # Non-flood members get zero impact
        for member in all_members:
            if member not in flood_members:
                rp_dict = {rp: 0.0 for rp in settings.flood_detect_rps}
                for unit in unit_names:
                    impact_cube.setdefault(unit, {}).setdefault(lead_days, {}).setdefault(
                        member, rp_dict
                    )

    grbs.close()
    logger.info(
        "Step 2 complete — %d units, %d lead days, %d members",
        len(impact_cube), len(lead_days_list), len(all_members),
    )
    return impact_cube, all_members, lead_days_list
