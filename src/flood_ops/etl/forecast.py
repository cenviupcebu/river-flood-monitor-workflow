"""Step 4 — Evaluate: compute ensemble probability of exceeding OEP thresholds."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, Iterable
import re
import tempfile
from contextlib import ExitStack
import numbers
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

from .step3_impact import (
    EventPatchImpactInput,
    ImpactCube,
    compute_impacts_from_event_patches,
)

import numpy as np
import pandas as pd
from flood_ops.etl.run_spec import DetectionSettings, DecisionSettings
from .utils import TierDecision, UnitDecision

logger = get_logger(__name__)
from flood_ops.logging import get_logger


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
"""Step 3 — Impact: compute population affected per member/lead/unit.

This module provides:

1) ``compute_impacts_from_event_patches`` for operational mode. It calls
    ``philflood.models.impact.population_exposure.aggregate_affected_population``
    on each event patch detected in Step 2 using the patch depth raster and
    WorldPop grid.
2) ``load_precomputed_impacts`` as a compatibility bridge for prototype mode
    where impacts are pre-exported to JSON.
"""
"""Step 4 — Evaluate: compute ensemble probability of exceeding OEP thresholds."""

"""Step 5 — Decide: apply tier rules with persistence and minimum-lead constraints."""


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_RP_CAP = 500.0


# Type alias: unit → lead → member → rp → people
ImpactCube = Dict[str, Dict[int, Dict[int, Dict[int, float]]]]

def forecast(
    extracted: Dict[str, Any],
    issue_date: date,
    run_spec: PipelineRunSpec,
) -> Dict[str, Any]:
    """Run flood detection, exceedance, and alert rule evaluation."""
    basin_id = str(extracted["basin_id"])
    logger.info("Starting evaluation — basin='%s', issue_date=%s", basin_id, issue_date)

    impact_cube, members, _ = detect_flood_events(
        forecast_path=str(extracted["forecast_path"]),
        evt_params_path=extracted["evt_parquet"],
        oep_json_path=extracted["oep_path"],
        issue_date=issue_date,
        basin_id=basin_id,
        settings=extracted["det"],
    )
    impacts_source = f"step2_detect:{extracted['forecast_path']}"
    logger.info("Detection mode complete — impact cube has %d units", len(impact_cube))

    prob_exceed = _compute_prob_exceed(impact_cube, extracted["thresholds"], members)
    units: List[UnitDecision] = _apply_tier_rules(
        prob_exceed,
        extracted["thresholds"],
        run_spec.decision,
    )

    logger.info(
        "Evaluation complete — basin='%s', %d units evaluated",
        basin_id,
        len(units),
    )
    return {
        "basin_id": basin_id,
        "forecast_path": extracted["forecast_path"],
        "oep_path": extracted["oep_path"],
        "units": units,
        "impacts_source": impacts_source,
    }


def _find_latest_persistent_lead(
    firing_leads: Iterable[int],
    min_lead: int,
    persist_days: int,
) -> Optional[int]:
    """Return the latest lead day satisfying both persistence and minimum lead.

    A lead qualifies when it is part of a contiguous window of
    ``persist_days`` consecutive integer lead days all of which appear in
    ``firing_leads``, and the lead is >= ``min_lead``.

    Returns the latest (highest) such lead for maximum warning time.
    """
    if persist_days <= 1:
        eligible = [ld for ld in firing_leads if ld >= min_lead]
        return max(eligible) if eligible else None

    firing_set = set(int(ld) for ld in firing_leads)
    eligible = sorted([ld for ld in firing_set if ld >= min_lead], reverse=True)

    for lead in eligible:
        for start in range(lead - persist_days + 1, lead + 1):
            window = {start + step for step in range(persist_days)}
            if lead in window and window.issubset(firing_set):
                return lead
    return None


def _apply_tier_rules(
    prob_exceed: Dict[str, Dict[int, Dict[int, float]]],
    thresholds: Dict[str, Dict[int, float]],
    decision: DecisionSettings,
) -> List[UnitDecision]:
    """Evaluate all tier rules across all units.

    Parameters
    ----------
    prob_exceed:
        Exceedance probability cube — unit → lead → rp → probability.
    thresholds:
        OEP thresholds — unit → rp → impact_threshold_people.
    decision:
        Policy settings (persist_days, min_lead, rule list).

    Returns
    -------
    list of UnitDecision
    """
    logger.info(
        "Applying %d tier rules to %d units (persist_days=%d, min_lead=%d)",
        len(decision.rules),
        len(prob_exceed),
        decision.persist_days,
        decision.min_lead,
    )
    units: List[UnitDecision] = []
    fired_count = 0

    for unit_id, lead_map in prob_exceed.items():
        tier_results: List[TierDecision] = []
        for rule in decision.rules:
            firing = [
                lead
                for lead, rp_prob in lead_map.items()
                if rp_prob.get(rule.rp, 0.0) >= rule.p_thr
            ]
            fire_lead = _find_latest_persistent_lead(
                firing,
                min_lead=decision.min_lead,
                persist_days=decision.persist_days,
            )
            prob_at_fire: Optional[float] = None
            if fire_lead is not None:
                prob_at_fire = lead_map.get(fire_lead, {}).get(rule.rp)
                fired_count += 1
                logger.info(
                    "Tier %s FIRED — unit='%s', fire_lead=%d, p=%.2f",
                    rule.name,
                    unit_id,
                    fire_lead,
                    prob_at_fire or 0.0,
                )

            tier_results.append(
                TierDecision(
                    tier=rule.name,
                    rp=rule.rp,
                    p_threshold=rule.p_thr,
                    fired=fire_lead is not None,
                    fire_lead=fire_lead,
                    probability_at_fire=prob_at_fire,
                    impact_threshold_people=thresholds.get(unit_id, {}).get(rule.rp),
                )
            )
        units.append(UnitDecision(unit_id=unit_id, tiers=tier_results))

    logger.info(
        "Tier evaluation complete: %d units, %d tier decisions fired",
        len(units),
        fired_count,
    )
    return units


@dataclass
class EventPatchImpactInput:
    """Input payload for one Step 2 event patch passed to Step 3.

    Parameters
    ----------
    lead_day:
        Forecast lead day for this patch.
    member_id:
        Ensemble member identifier for this patch.
    rp:
        Return period bucket that this patch contributes to.
    depth_raster:
        Path to the flood depth raster for this patch (NB02-style output).
    event_id:
        Optional patch/event identifier used for traceability.
    extra:
        Optional payload forwarded to philflood's aggregator when supported.
    """

    lead_day: int
    member_id: int
    rp: int
    depth_raster: Path
    event_id: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)


def _unit_key(unit_name: str) -> str:
    unit = str(unit_name).strip()
    if not unit:
        return ""
    if "::" in unit:
        return unit
    return f"ADM3::{unit}"


def _normalise_aggregated_rows(result: Any) -> Dict[str, float]:
    """Normalise aggregator output to {unit_key: affected_people}.

    Supports dict-like and dataframe-like outputs to keep Step 3 tolerant to
    implementation differences across philflood versions.
    """
    out: Dict[str, float] = {}

    if isinstance(result, Mapping):
        for key, value in result.items():
            if isinstance(value, numbers.Number):
                unit = _unit_key(str(key))
                if unit:
                    out[unit] = out.get(unit, 0.0) + float(value)
        return out

    # DataFrame-like output handling without requiring pandas type imports.
    if hasattr(result, "iterrows"):
        for _, row in result.iterrows():
            unit = ""
            for col in ("unit_id", "unit", "adm3_name", "name"):
                if col in row and str(row[col]).strip():
                    unit = _unit_key(str(row[col]))
                    break

            pop_val = None
            for col in ("affected_pop", "affected_population", "population", "people"):
                if col in row:
                    pop_val = row[col]
                    break

            if unit and isinstance(pop_val, numbers.Number):
                out[unit] = out.get(unit, 0.0) + float(pop_val)

    return out


def _call_population_aggregator(
    patch: EventPatchImpactInput,
    worldpop_tif: Path,
    depth_threshold_m: float,
) -> Dict[str, float]:
    """Invoke philflood aggregate_affected_population for one event patch."""
    import inspect
    import importlib

    try:
        mod = importlib.import_module("philflood.models.impact.population_exposure")
        aggregate_affected_population = getattr(mod, "aggregate_affected_population")
    except ModuleNotFoundError as exc:
        raise ModuleNotFoundError(
            "Step 3 requires philflood.models.impact.population_exposure."
        ) from exc

    candidates: Dict[str, Any] = {
        "depth_raster": str(patch.depth_raster),
        "depth_tif": str(patch.depth_raster),
        "flood_depth_raster": str(patch.depth_raster),
        "flood_depth_tif": str(patch.depth_raster),
        "worldpop_tif": str(worldpop_tif),
        "worldpop_grid": str(worldpop_tif),
        "population_raster": str(worldpop_tif),
        "depth_threshold_m": float(depth_threshold_m),
        "threshold_m": float(depth_threshold_m),
        "event_id": patch.event_id or f"lead{patch.lead_day}_m{patch.member_id}_rp{patch.rp}",
        "rp": int(patch.rp),
    }
    candidates.update(patch.extra)

    sig = inspect.signature(aggregate_affected_population)
    accepts_var_kw = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values())
    if accepts_var_kw:
        payload = candidates
    else:
        payload = {
            name: value
            for name, value in candidates.items()
            if name in sig.parameters
        }

    result = aggregate_affected_population(**payload)
    return _normalise_aggregated_rows(result)


def _compute_impacts_from_event_patches(
    patches: Iterable[EventPatchImpactInput],
    worldpop_tif: Path,
    depth_threshold_m: float = 0.02,
) -> Tuple[List[int], List[int], ImpactCube]:
    """Build an ``ImpactCube`` by aggregating people affected per event patch.

    Each patch is processed independently and mapped into
    ``cube[unit][lead_day][member_id][rp]``.
    """
    worldpop_tif = Path(worldpop_tif)
    if not worldpop_tif.exists():
        raise FileNotFoundError(f"WorldPop raster not found: {worldpop_tif}")

    patch_list = list(patches)
    members_seen = set()
    leads_seen = set()
    cube: ImpactCube = {}
    n_ok = 0

    for patch in patch_list:
        print('patch: ', patch)
        lead = int(patch.lead_day)
        print('lead: ', lead)
        member = int(patch.member_id)
        print('member: ', member)
        rp = int(patch.rp)
        if lead <= 0 or member <= 0 or rp <= 0:
            logger.warning("Skipping patch with invalid lead/member/rp: %s", patch)
            continue

        if not Path(patch.depth_raster).exists():
            logger.warning("Skipping patch, depth raster not found: %s", patch.depth_raster)
            continue

        unit_impacts = _call_population_aggregator(
            patch=patch,
            worldpop_tif=worldpop_tif,
            depth_threshold_m=depth_threshold_m,
        )
        print('unit_impacts: ', unit_impacts)
        for unit, affected_people in unit_impacts.items():
            per_rp = cube.setdefault(unit, {}).setdefault(lead, {}).setdefault(member, {})
            per_rp[rp] = per_rp.get(rp, 0.0) + float(affected_people)

        members_seen.add(member)
        leads_seen.add(lead)
        n_ok += 1

    members = sorted(members_seen)
    leads = sorted(leads_seen)
    logger.info(
        "Computed impacts from %d/%d event patches: %d members, %d lead days, %d units",
        n_ok,
        len(patch_list),
        len(members),
        len(leads),
        len(cube),
    )
    return members, leads, cube


def _load_precomputed_impacts(
    impacts_path: Path,
) -> Tuple[List[int], List[int], ImpactCube]:
    """Load an ensemble impact cube from a JSON file.

    Expected schema::

        {
          "ensemble_members": [1, 2, ...],
          "lead_days": [1, 2, ...],
          "records": [
            {
              "unit_id": "ADM3::Name",
              "lead_day": 5,
              "member_id": 1,
              "rp_affected_people": {"2": 123.0, "5": 77.0, "10": 20.0}
            }
          ]
        }

    Returns
    -------
    (ensemble_members, lead_days, cube)
    """
    logger.info("Loading precomputed impacts from %s", impacts_path)
    raw = json.loads(impacts_path.read_text(encoding="utf-8"))
    members = [int(m) for m in raw.get("ensemble_members", [])]
    leads = [int(ld) for ld in raw.get("lead_days", [])]

    cube: ImpactCube = {}
    n_records = 0
    for rec in raw.get("records", []):
        unit = str(rec.get("unit_id", ""))
        if not unit:
            continue
        lead = int(rec.get("lead_day", 0))
        member = int(rec.get("member_id", 0))
        if lead <= 0 or member <= 0:
            continue

        rp_dict: Dict[int, float] = {}
        for rp_raw, value in (rec.get("rp_affected_people") or {}).items():
            try:
                rp_dict[int(float(rp_raw))] = float(value)
            except (TypeError, ValueError):
                continue

        cube.setdefault(unit, {}).setdefault(lead, {})[member] = rp_dict
        n_records += 1

    logger.info(
        "Loaded impact cube: %d members, %d lead days, %d records across %d units",
        len(members),
        len(leads),
        n_records,
        len(cube),
    )
    return members, leads, cube

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


def _build_netcdf_index(
    ds,
    var_name: str,
    time_dim: str,
    member_dim: Optional[str],
) -> dict:
    """Index NetCDF forecast snapshots to match the GRIB lookup contract."""
    import pandas as pd

    msg_index: dict = {}
    vt_lookup: dict = {}
    steps: set = set()

    times = pd.to_datetime(ds[time_dim].values)
    if member_dim and member_dim in ds[var_name].dims:
        raw_members = ds[member_dim].values.tolist()
        members = [int(m) for m in raw_members]
        member_to_idx = {int(m): i for i, m in enumerate(raw_members)}
    else:
        members = [0]
        member_to_idx = {0: None}

    if len(times) == 0:
        raise RuntimeError("NetCDF has no forecast times to index")

    t0 = pd.Timestamp(times[0])
    for t_idx, vt in enumerate(times):
        vt_ts = pd.Timestamp(vt)
        step_h = int((vt_ts - t0).total_seconds() // 3600)
        steps.add(step_h)
        for m in members:
            key = (vt_ts, m)
            msg_index[key] = (t_idx, member_to_idx[m])
            vt_lookup[(vt_ts, m)] = (key, None)

    return {
        "msg_index": msg_index,
        "inits": [],
        "steps": sorted(steps),
        "members": sorted(members),
        "tmpl": None,
        "vt_lookup": vt_lookup,
    }


def _open_forecast_source(forecast_path: Path, shortname: str):
    """Open forecast source from NetCDF via xarray."""
    import numpy as np
    import xarray as xr

    suffixes = {s.lower() for s in forecast_path.suffixes}
    is_netcdf = bool(suffixes.intersection({".nc", ".nc4", ".netcdf"}))

    if not is_netcdf:
        raise RuntimeError(
            f"Only NetCDF forecast input is supported for now: {forecast_path}"
        )

    ds = xr.open_dataset(forecast_path)

    var_candidates = [shortname, "dis24", "dis", "discharge"]
    var_name = next((v for v in var_candidates if v in ds.data_vars), None)
    if var_name is None and ds.data_vars:
        var_name = next(iter(ds.data_vars))
    if var_name is None:
        ds.close()
        raise RuntimeError(f"No data variables found in NetCDF: {forecast_path}")

    da = ds[var_name]
    lat_dim = next((d for d in ("latitude", "lat") if d in da.dims), None)
    lon_dim = next((d for d in ("longitude", "lon") if d in da.dims), None)
    time_dim = next((d for d in ("valid_time", "time") if d in da.dims), None)
    member_dim = next(
        (d for d in ("number", "member", "ensemble", "perturbationNumber") if d in da.dims),
        None,
    )

    if lat_dim is None or lon_dim is None or time_dim is None:
        ds.close()
        raise RuntimeError(
            f"NetCDF variable '{var_name}' must include latitude/longitude/time dimensions"
        )

    lat_vals = np.asarray(ds[lat_dim].values)
    lon_vals = np.asarray(ds[lon_dim].values)
    if lat_vals.ndim == 2:
        lat_vals = lat_vals[:, 0]
    if lon_vals.ndim == 2:
        lon_vals = lon_vals[0, :]

    nc_index = _build_netcdf_index(ds, var_name, time_dim, member_dim)
    source = {
        "kind": "netcdf",
        "ds": ds,
        "var_name": var_name,
        "time_dim": time_dim,
        "member_dim": member_dim,
    }
    return source, nc_index, lat_vals.astype(float), lon_vals.astype(float)


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
        if isinstance(grbs, dict) and grbs.get("kind") == "netcdf":
            ds = grbs["ds"]
            da = ds[grbs["var_name"]]
            t_idx, m_idx = msg_num
            sel = {grbs["time_dim"]: t_idx}
            if grbs["member_dim"] is not None and m_idx is not None:
                sel[grbs["member_dim"]] = m_idx
            data = da.isel(sel).values
        else:
            msg = grbs.message(msg_num)
            data = msg.values
        return data[cell_lat_idx, cell_lon_idx].astype(float)
    except Exception as exc:
        logger.debug("GRIB read error for key %s: %s", key_data, exc)
        return None


# ---------------------------------------------------------------------------
# Connected-component flood detection  (NB07 Cell 3 detect_flood_members)
# ---------------------------------------------------------------------------

def _detect_flood_patches_for_lead(
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
    support_lat_vals,
    support_lon_vals,
) -> Tuple[List[int], Dict[int, List[Dict[str, Any]]]]:
    """Phase 1: detect flood members and record qualifying event patches.

    Returns
    -------
    (flood_members, patches_by_member)
        patches_by_member[member] = list of patch dicts with valid_date and bbox.
    """
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
    patches_by_member: Dict[int, List[Dict[str, Any]]] = {}

    for member in grib_index["members"]:
        member_patches: List[Dict[str, Any]] = []
        for vdate in lead_window:
            q_vals = _read_grib_snapshot(
                grbs, grib_index["msg_index"], grib_index["vt_lookup"],
                vdate, member, init_dt, cell_lat_idx, cell_lon_idx,
            ) * 1000  # TODO: *1000 multipler to mock for trigger case. To remove when operating
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
            if len(areas) <= 1:
                continue
            for lab_id in range(1, len(areas)):
                if areas[lab_id] < a_min_km2:
                    continue
                patch_mask = labs == lab_id
                pos = np.argwhere(patch_mask)
                if pos.size == 0:
                    continue

                lat_idx = pos[:, 0]
                lon_idx = pos[:, 1]
                lat_min = float(np.min(support_lat_vals[lat_idx]))
                lat_max = float(np.max(support_lat_vals[lat_idx]))
                lon_min = float(np.min(support_lon_vals[lon_idx]))
                lon_max = float(np.max(support_lon_vals[lon_idx]))

                member_patches.append(
                    {
                        "valid_date": vdate,
                        "area_km2": float(areas[lab_id]),
                        "bbox": (lon_min, lat_min, lon_max, lat_max),
                    }
                )

        if member_patches:
            flood_members.append(member)
            patches_by_member[member] = member_patches
    return flood_members, patches_by_member


def _render_depth_raster_for_patch(
    discharge_per_cell,
    evt_params,
    spatial: dict,
    bbox: Tuple[float, float, float, float],
    patch_bbox: Tuple[float, float, float, float],
    out_tif: Path,
) -> bool:
    """Build and write an event depth raster clipped to one detected patch bbox."""
    import numpy as np
    import pandas as pd
    import rasterio
    import xarray as xr
    from shapely.geometry import box as sbox

    ep = evt_params.set_index("cell_id")
    cells = [c for c in discharge_per_cell.index if c in ep.index]
    if not cells:
        return False

    q = discharge_per_cell[cells].fillna(0.0).values[None, :]
    rp_vals = discharge_to_return_period(
        q,
        ep.loc[cells, "u"].values[None, :],
        ep.loc[cells, "sigma"].values[None, :],
        ep.loc[cells, "xi"].values[None, :],
        ep.loc[cells, "lam"].values[None, :],
    ).ravel()
    if np.nanmax(rp_vals) < 2.0:
        return False

    rp_df = pd.DataFrame(
        {
            "lat": ep.loc[cells, "lat"].values,
            "lon": ep.loc[cells, "lon"].values,
            "rp": rp_vals,
        }
    )
    lat_vals = np.sort(rp_df["lat"].unique())
    lon_vals = np.sort(rp_df["lon"].unique())
    grid = np.full((len(lat_vals), len(lon_vals)), np.nan, dtype="float32")
    lat_idx = {v: i for i, v in enumerate(lat_vals)}
    lon_idx = {v: j for j, v in enumerate(lon_vals)}
    for _, row in rp_df.iterrows():
        grid[lat_idx[row["lat"]], lon_idx[row["lon"]]] = row["rp"]
    _ = xr.DataArray(
        grid,
        coords={"latitude": lat_vals, "longitude": lon_vals},
        dims=("latitude", "longitude"),
        name="rp",
    )

    rp_to_files = spatial["rp_to_files"]
    minx, miny, maxx, maxy = bbox
    bbox_geom = sbox(minx, miny, maxx, maxy)
    available_rps = sorted(rp for rp in rp_to_files if rp_to_files[rp])
    if not available_rps:
        return False

    from rasterio.merge import merge

    depth_arrays: Dict[int, np.ndarray] = {}
    ref_transform = None
    ref_crs = None
    ref_shape: Optional[Tuple[int, int]] = None
    for rp in available_rps:
        tif_paths = [p for p in rp_to_files[rp] if sbox(*rasterio.open(p).bounds).intersects(bbox_geom)]
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
                with rasterio.open(tif_paths[0]) as src_ref:
                    ref_crs = src_ref.crs
            depth_arrays[rp] = arr
        except Exception as exc:
            logger.debug("JRC load error RP%d: %s", rp, exc)

    if not depth_arrays or ref_shape is None or ref_transform is None:
        return False

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

    h, w = ref_shape
    px_x = float(ref_transform.a)
    px_y = float(ref_transform.e)
    x0 = float(ref_transform.c)
    y0 = float(ref_transform.f)
    lon_centers = x0 + (np.arange(w) + 0.5) * px_x
    lat_centers = y0 + (np.arange(h) + 0.5) * px_y

    p_minx, p_miny, p_maxx, p_maxy = patch_bbox
    keep_x = (lon_centers >= p_minx) & (lon_centers <= p_maxx)
    keep_y = (lat_centers >= p_miny) & (lat_centers <= p_maxy)
    keep_mask = np.outer(keep_y, keep_x)
    depth_np = np.where(keep_mask, depth_np, np.nan)

    if not np.isfinite(depth_np).any():
        return False

    out_tif.parent.mkdir(parents=True, exist_ok=True)
    nodata_val = np.float32(-9999.0)
    write_arr = np.where(np.isfinite(depth_np), depth_np, nodata_val).astype(np.float32)
    with rasterio.open(
        out_tif,
        "w",
        driver="GTiff",
        height=h,
        width=w,
        count=1,
        dtype="float32",
        crs=ref_crs,
        transform=ref_transform,
        nodata=nodata_val,
    ) as dst:
        dst.write(write_arr, 1)
    return True


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
    import numpy as np
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
        "gpd_sigma": "sigma",
        "gpd_scale_sigma": "sigma", #TODO: check duplicate sigma origin
    }
    evt_params = evt_raw.rename(
        columns={k: v for k, v in _col_map.items() if k in evt_raw.columns}
    ).copy()
    required_evt_cols = {"cell_id", "u", "lam", "xi", "sigma"}
    missing_evt_cols = sorted(required_evt_cols.difference(set(evt_params.columns)))
    if missing_evt_cols:
        raise ValueError(
            "EVT parameters file is missing required columns after normalization: "
            f"{missing_evt_cols}. Available columns: {list(evt_raw.columns)}"
        )
    if "lat" not in evt_params.columns or "lon" not in evt_params.columns:
        ll = evt_params["cell_id"].astype(str).apply(_parse_cell_coords)
        evt_params["lat"] = [v[0] for v in ll]
        evt_params["lon"] = [v[1] for v in ll]

    basin_cells = evt_params["cell_id"].astype(str).tolist()
    logger.info("EVT1 params loaded: %d cells for basin '%s'", len(basin_cells), basin_id)

    # --- Build support grid --------------------------------------------------
    lat_vals, lon_vals, grid_index, area_grid, _, _ = _build_support_grid(evt_params)

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

    # --- Open forecast source (.nc/.nc4) -----------------------------------
    grib_path = Path(forecast_path)
    if not grib_path.exists():
        raise FileNotFoundError(f"Forecast file not found: {grib_path}")

    try:
        forecast_src, grib_index, grib_lat1d, grib_lon1d = _open_forecast_source(
            grib_path, settings.grib_shortname
        )
    except RuntimeError as exc:
        raise RuntimeError(
            f"Failed to index forecast data {grib_path}: {exc}"
        ) from exc

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
    with tempfile.TemporaryDirectory(prefix=f"step3_patch_{basin_id}_") as patch_dir:
        patch_dir_path = Path(patch_dir)
        for lead_days in lead_days_list[:3]:  # loop only 3 first lead times for faster run. TODO: remove slice to run all leads
            lead_window = pd.date_range(
                init_dt + pd.Timedelta(days=1),
                init_dt + pd.Timedelta(days=lead_days),
                freq="D",
            )

            # Phase 1: detect flood members and event patches via connected components
            flood_members, patches_by_member = _detect_flood_patches_for_lead(
                grbs=forecast_src,
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
                support_lat_vals=lat_vals,
                support_lon_vals=lon_vals,
            )

            n_patches = sum(len(v) for v in patches_by_member.values())
            logger.info(
                f"Lead {lead_days:2d}d: "
                f"{len(flood_members)}/{len(all_members)} flood members, "
                f"{n_patches} qualifying patches"
            )

            lead_patch_inputs: List[EventPatchImpactInput] = []
            for member in flood_members:
                for patch_idx, patch_meta in enumerate(patches_by_member.get(member, []), start=1):
                    vdate = patch_meta["valid_date"]
                    q_vals = _read_grib_snapshot(
                        forecast_src,
                        grib_index["msg_index"],
                        grib_index["vt_lookup"],
                        vdate,
                        member,
                        init_dt,
                        cell_lat_idx,
                        cell_lon_idx,
                    ) * 1000  # TODO: *1000 multipler to mock for trigger case. To remove when operating
                    if q_vals is None:
                        continue

                    q_snapshot = pd.Series(q_vals, index=basin_cells)
                    depth_raster = patch_dir_path / (
                        f"{basin_id}_lead{lead_days:02d}_m{member:03d}_patch{patch_idx:03d}.tif"
                    )
                    ok = _render_depth_raster_for_patch(
                        discharge_per_cell=q_snapshot,
                        evt_params=evt_params,
                        spatial=spatial,
                        bbox=bbox,
                        patch_bbox=patch_meta["bbox"],
                        out_tif=depth_raster,
                    )
                    if not ok:
                        continue

                    patch_event_id = (
                        f"{basin_id}_lead{lead_days:02d}_m{member:03d}_patch{patch_idx:03d}"
                    )
                    for rp in settings.flood_detect_rps:
                        lead_patch_inputs.append(
                            EventPatchImpactInput(
                                lead_day=lead_days,
                                member_id=member,
                                rp=int(rp),
                                depth_raster=depth_raster,
                                event_id=f"{patch_event_id}_rp{int(rp)}",
                            )
                        )

            if lead_patch_inputs:
                _, _, lead_cube = compute_impacts_from_event_patches(
                    patches=lead_patch_inputs,
                    worldpop_tif=Path(settings.worldpop_tif),
                    depth_threshold_m=settings.depth_threshold_m,
                )

                for unit, by_lead in lead_cube.items():
                    for lead, by_member in by_lead.items():
                        for member, by_rp in by_member.items():
                            dst_rp = (
                                impact_cube
                                .setdefault(unit, {})
                                .setdefault(lead, {})
                                .setdefault(member, {})
                            )
                            for rp, val in by_rp.items():
                                dst_rp[int(rp)] = dst_rp.get(int(rp), 0.0) + float(val)

            # Ensure full cube coverage (all unit/member/rp combinations) for this lead.
            for member in all_members:
                for unit in unit_names:
                    dst_rp = impact_cube.setdefault(unit, {}).setdefault(lead_days, {}).setdefault(member, {})
                    for rp in settings.flood_detect_rps:
                        dst_rp.setdefault(int(rp), 0.0)

    if isinstance(forecast_src, dict) and forecast_src.get("kind") == "netcdf":
        forecast_src["ds"].close()
    else:
        forecast_src["grbs"].close()
    logger.info(
        "Step 2 complete — %d units, %d lead days, %d members",
        len(impact_cube), len(lead_days_list), len(all_members),
    )
    return impact_cube, all_members, lead_days_list


def _compute_prob_exceed(
    cube: Dict[str, Dict[int, Dict[int, Dict[int, float]]]],
    thresholds: Dict[str, Dict[int, float]],
    members: Iterable[int],
) -> Dict[str, Dict[int, Dict[int, float]]]:
    """Compute the fraction of ensemble members exceeding OEP thresholds.

    Returns
    -------
    dict
        ``{unit_id: {lead_day: {rp: probability}}}``
    """
    member_set = sorted(set(int(m) for m in members))
    n_members = len(member_set)
    if n_members == 0:
        logger.warning("No ensemble members — returning empty exceedance dict")
        return {}

    logger.info(
        "Computing exceedance probabilities: %d cube units, %d ensemble members",
        len(cube),
        n_members,
    )
    out: Dict[str, Dict[int, Dict[int, float]]] = {}
    for unit, per_lead in cube.items():
        unit_thresholds = thresholds.get(unit, {})
        if not unit_thresholds:
            continue
        for lead, per_member in per_lead.items():
            for rp, thr in unit_thresholds.items():
                exceed = sum(
                    1
                    for member in member_set
                    if per_member.get(member, {}).get(rp, 0.0) >= thr
                )
                out.setdefault(unit, {}).setdefault(lead, {})[rp] = exceed / n_members

    logger.info("Exceedance probabilities computed for %d qualifying units", len(out))
    return out
