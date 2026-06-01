"""Step 4 — Evaluate: compute ensemble probability of exceeding OEP thresholds."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, Iterable

from flood_ops.logging import get_logger

logger = get_logger(__name__)


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
