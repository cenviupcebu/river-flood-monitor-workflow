
from __future__ import annotations

import csv
import json
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Tuple

from flood_ops.logging import get_logger
from .run_spec import PipelineRunSpec
from .utils import BasinRunOutput, expand_template

logger = get_logger(__name__)


"""
Step 6 — Output: serialise trigger decisions for downstream systems.
"""


_CSV_FIELDS = [
    "issue_date",
    "basin_id",
    "unit_id",
    "tier",
    "rp",
    "p_threshold",
    "fired",
    "fire_lead",
    "probability_at_fire",
    "impact_threshold_people",
]


def save(
    run_spec: PipelineRunSpec,
    issue_date: date,
    basin_forecasts: List[Dict[str, Any]],
) -> Tuple[List[BasinRunOutput], Path]:
    """Format basin outputs and persist final trigger files."""
    basin_results: List[BasinRunOutput] = []
    for basin in basin_forecasts:
        metadata = {
            "rule_tiers": [
                {
                    "name": r.name,
                    "rp": r.rp,
                    "p_thr": r.p_thr,
                    "n_req": r.n_req,
                    "label": r.label,
                }
                for r in run_spec.decision.rules
            ],
            "persist_days": run_spec.decision.persist_days,
            "min_lead": run_spec.decision.min_lead,
            "oep_min": run_spec.decision.oep_min,
            "oep_source": str(basin["oep_path"]),
            "impacts_source": basin["impacts_source"],
            "detection_mode": "step2_detect",
        }

        basin_results.append(
            BasinRunOutput(
                basin_id=str(basin["basin_id"]),
                issue_date=issue_date.isoformat(),
                forecast_path=str(basin["forecast_path"]),
                units=basin["units"],
                metadata=metadata,
            )
        )

    output_file = _write_outputs(run_spec, issue_date, basin_results)
    return basin_results, output_file


def _serialise_basin(result: BasinRunOutput) -> Dict[str, Any]:
    return {
        "basin_id": result.basin_id,
        "issue_date": result.issue_date,
        "forecast_path": result.forecast_path,
        "metadata": result.metadata,
        "units": [
            {
                "unit_id": unit.unit_id,
                "tiers": [
                    {
                        "tier": tier.tier,
                        "rp": tier.rp,
                        "p_threshold": tier.p_threshold,
                        "fired": tier.fired,
                        "fire_lead": tier.fire_lead,
                        "probability_at_fire": tier.probability_at_fire,
                        "impact_threshold_people": tier.impact_threshold_people,
                    }
                    for tier in unit.tiers
                ],
            }
            for unit in result.units
        ],
    }


def _write_outputs(
    run_spec: PipelineRunSpec,
    issue_date: date,
    basin_results: List[BasinRunOutput],
) -> Path:
    """Write trigger decisions as JSON (default) or flat CSV."""
    if run_spec.output is None:
        raise ValueError("Run spec must define output.output_dir_template")

    output_dir = Path(expand_template(run_spec.output.output_dir_template, issue_date))
    output_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    output_format = run_spec.output.format.lower()

    logger.info(
        "Writing output: %d basins, format=%s, dir=%s",
        len(basin_results),
        output_format,
        output_dir,
    )

    if output_format == "csv":
        out_file = output_dir / f"trigger_decisions_{issue_date.isoformat()}_{timestamp}.csv"
        with open(out_file, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=_CSV_FIELDS)
            writer.writeheader()
            for basin in basin_results:
                for unit in basin.units:
                    for tier in unit.tiers:
                        writer.writerow(
                            {
                                "issue_date": basin.issue_date,
                                "basin_id": basin.basin_id,
                                "unit_id": unit.unit_id,
                                "tier": tier.tier,
                                "rp": tier.rp,
                                "p_threshold": tier.p_threshold,
                                "fired": tier.fired,
                                "fire_lead": tier.fire_lead,
                                "probability_at_fire": tier.probability_at_fire,
                                "impact_threshold_people": tier.impact_threshold_people,
                            }
                        )
        logger.info("CSV output written: %s", out_file)
        return out_file

    out_file = output_dir / f"trigger_decisions_{issue_date.isoformat()}_{timestamp}.json"
    payload = {
        "run_name": run_spec.run_name,
        "generated_at_utc": datetime.utcnow().isoformat() + "Z",
        "issue_date": issue_date.isoformat(),
        "basins": [_serialise_basin(b) for b in basin_results],
    }
    out_file.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    logger.info("JSON output written: %s", out_file)
    return out_file
