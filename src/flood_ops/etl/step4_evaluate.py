"""Step 4 — Evaluate: compute ensemble probability of exceeding OEP thresholds."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, Iterable

from flood_ops.logging import get_logger

logger = get_logger(__name__)


def load_oep_thresholds(
    oep_json_path: Path,
    oep_min: float,
) -> Dict[str, Dict[int, float]]:
    """Load per-unit OEP impact thresholds from the NB05 JSON.

    Units whose RP2 threshold is below ``oep_min`` are excluded.

    Returns
    -------
    dict
        ``{unit_id: {rp: threshold_people}}``
    """
    logger.info(
        "Loading OEP thresholds from %s (oep_min=%.0f)", oep_json_path, oep_min
    )
    if not oep_json_path.exists():
        raise FileNotFoundError(f"OEP JSON not found: {oep_json_path}")

    raw = json.loads(oep_json_path.read_text(encoding="utf-8"))
    rp_report = [int(float(x)) for x in raw.get("rp_report", [])]

    thresholds: Dict[str, Dict[int, float]] = {}
    for rec in raw.get("units", []):
        unit = rec.get("unit")
        if not unit:
            continue
        oep_rl = rec.get("oep_rl", [])
        rp_map: Dict[int, float] = {}
        for idx, rp in enumerate(rp_report):
            if idx >= len(oep_rl):
                continue
            try:
                rp_map[rp] = float(oep_rl[idx])
            except (TypeError, ValueError):
                continue
        if rp_map.get(2, 0.0) >= oep_min:
            thresholds[str(unit)] = rp_map

    logger.info(
        "OEP thresholds loaded: %d qualifying units (from %d total, oep_min=%.0f)",
        len(thresholds),
        len(raw.get("units", [])),
        oep_min,
    )
    return thresholds


def compute_prob_exceed(
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
