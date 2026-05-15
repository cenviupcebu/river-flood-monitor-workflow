"""Step 3 — Impact: load or compute population affected per member/lead/unit.

Prototype mode
--------------
Reads a precomputed impact cube JSON exported from NB07 or
``calibration/scripts/run_reforecast_month.py``.  This bridges Steps 2-3
while the real-time detection algorithm is not yet wired in.

Future integration
------------------
When Step 2 is implemented, replace ``load_precomputed_impacts`` with a
function that calls::

    philflood.models.impact.population_exposure.aggregate_affected_population()

against the flood depth rasters (NB02) and WorldPop grid for each event
patch identified by the detection step.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Tuple

from flood_ops.logging import get_logger

logger = get_logger(__name__)

# Type alias: unit → lead → member → rp → people
ImpactCube = Dict[str, Dict[int, Dict[int, Dict[int, float]]]]


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
