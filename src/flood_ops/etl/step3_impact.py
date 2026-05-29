"""Step 3 — Impact: compute population affected per member/lead/unit.

This module provides:

1) ``compute_impacts_from_event_patches`` for operational mode. It calls
    ``philflood.models.impact.population_exposure.aggregate_affected_population``
    on each event patch detected in Step 2 using the patch depth raster and
    WorldPop grid.
2) ``load_precomputed_impacts`` as a compatibility bridge for prototype mode
    where impacts are pre-exported to JSON.
"""

from __future__ import annotations

import json
import numbers
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Tuple

from flood_ops.logging import get_logger

logger = get_logger(__name__)

# Type alias: unit → lead → member → rp → people
ImpactCube = Dict[str, Dict[int, Dict[int, Dict[int, float]]]]


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


def compute_impacts_from_event_patches(
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


def load_precomputed_impacts(
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
