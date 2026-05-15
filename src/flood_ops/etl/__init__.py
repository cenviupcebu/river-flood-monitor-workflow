"""Daily flood monitoring ETL pipeline — public entry points.

Module layout
-------------
utils.py          Shared dataclasses and path-template helper.
run_spec.py       PipelineRunSpec settings dataclasses and YAML loader.
step1_ingest.py   Resolve or download the GloFAS ensemble forecast file.
step2_detect.py   Spatial flood-event detection from GRIB (stub — v1.0).
step3_impact.py   Load or compute population affected per member/lead/unit.
step4_evaluate.py Compute ensemble probability of exceeding OEP thresholds.
step5_decide.py   Apply tier rules with persistence and minimum-lead constraints.
step6_output.py   Serialise trigger decisions for downstream systems.

Public API
----------
>>> from flood_ops.etl import run_daily_monitoring_etl
>>> results, out_file = run_daily_monitoring_etl(issue_date, basin_files, run_spec_path)
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import List, Tuple

from flood_ops.config import load_basin_config
from flood_ops.logging import get_logger, setup_pipeline_file_log
from .run_spec import PipelineRunSpec, load_run_spec
from .step1_ingest import resolve_forecast_path
from .step3_impact import load_precomputed_impacts
from .step4_evaluate import compute_prob_exceed, load_oep_thresholds
from .step5_decide import apply_tier_rules
from .step6_output import write_outputs
from .utils import BasinRunOutput, UnitDecision, expand_template

logger = get_logger(__name__)

__all__ = [
    "evaluate_trigger_for_basin",
    "run_daily_monitoring_etl",
    "load_run_spec",
    "PipelineRunSpec",
    "BasinRunOutput",
    "UnitDecision",
]


def evaluate_trigger_for_basin(
    basin_id: str,
    issue_date: date,
    run_spec: PipelineRunSpec,
) -> BasinRunOutput:
    """Evaluate the flood trigger for one basin using the 6-step ETL."""
    logger.info("Starting evaluation — basin='%s', issue_date=%s", basin_id, issue_date)

    if run_spec.inputs is None:
        raise ValueError("Run spec must define inputs.oep_json")

    # Step 1 — Ingest
    forecast_path = resolve_forecast_path(run_spec, issue_date, basin_id)

    # Step 4a — Load OEP thresholds
    oep_path = Path(expand_template(run_spec.inputs.oep_json, issue_date, basin_id))
    thresholds = load_oep_thresholds(oep_path, run_spec.decision.oep_min)

    # Step 3 — Load precomputed impacts (prototype bridge for Steps 2-3)
    if not run_spec.inputs.precomputed_impacts_template:
        raise ValueError(
            "Run spec must define inputs.precomputed_impacts_template for prototype mode."
        )
    impacts_path = Path(
        expand_template(run_spec.inputs.precomputed_impacts_template, issue_date, basin_id)
    )
    if not impacts_path.exists():
        raise FileNotFoundError(
            f"Precomputed impacts not found for basin '{basin_id}': {impacts_path}"
        )
    members, _, impact_cube = load_precomputed_impacts(impacts_path)

    # Step 4b — Compute exceedance probabilities
    prob_exceed = compute_prob_exceed(impact_cube, thresholds, members)

    # Step 5 — Apply tier rules
    units: List[UnitDecision] = apply_tier_rules(prob_exceed, thresholds, run_spec.decision)

    metadata = {
        "rule_tiers": [
            {"name": r.name, "rp": r.rp, "p_thr": r.p_thr, "n_req": r.n_req, "label": r.label}
            for r in run_spec.decision.rules
        ],
        "persist_days": run_spec.decision.persist_days,
        "min_lead": run_spec.decision.min_lead,
        "oep_min": run_spec.decision.oep_min,
        "oep_source": str(oep_path),
        "impacts_source": str(impacts_path),
    }

    logger.info(
        "Evaluation complete — basin='%s', %d units evaluated", basin_id, len(units)
    )
    return BasinRunOutput(
        basin_id=basin_id,
        issue_date=issue_date.isoformat(),
        forecast_path=forecast_path,
        units=units,
        metadata=metadata,
    )


def run_daily_monitoring_etl(
    issue_date: date,
    basin_config_files: List[str],
    run_spec_path: str,
) -> Tuple[List[BasinRunOutput], Path]:
    """Execute the prototype ETL run for all requested basins.

    Attaches a dated ``logs/<run_name>_<datetime>.txt`` file handler before
    the first basin so the full run trace is persisted.
    """
    run_spec = load_run_spec(run_spec_path)

    log_dir_template = "logs"
    if run_spec.output is not None:
        log_dir_template = getattr(run_spec.output, "log_dir_template", "logs")
    log_file = setup_pipeline_file_log(
        log_dir=Path(expand_template(log_dir_template, issue_date)),
        run_name=run_spec.run_name,
    )

    logger.info(
        "run_daily_monitoring_etl started — run='%s', issue_date=%s, basins=%d, log=%s",
        run_spec.run_name,
        issue_date,
        len(basin_config_files),
        log_file,
    )

    basin_results: List[BasinRunOutput] = []
    for cfg_path in basin_config_files:
        cfg = load_basin_config(cfg_path)
        logger.info("Processing basin '%s' from %s", cfg.basin_id, cfg_path)
        basin_results.append(evaluate_trigger_for_basin(cfg.basin_id, issue_date, run_spec))

    # Step 6 — Write outputs
    output_file = write_outputs(run_spec, issue_date, basin_results)

    total_fired = sum(
        1 for basin in basin_results for unit in basin.units for tier in unit.tiers if tier.fired
    )
    logger.info(
        "run_daily_monitoring_etl complete — %d basins, %d tier fires, output=%s",
        len(basin_results),
        total_fired,
        output_file,
    )
    return basin_results, output_file
