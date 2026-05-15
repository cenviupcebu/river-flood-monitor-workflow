"""Step 0 orchestration for daily flood monitoring ETL.

This module hosts the high-level pipeline entry points that were previously
defined in ``etl.__init__``.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import List, Tuple

from flood_ops.config import load_basin_config
from flood_ops.logging import get_logger, setup_pipeline_file_log

from .run_spec import PipelineRunSpec, load_run_spec
from .step1_ingest import resolve_forecast_path
from .step2_detect import detect_flood_events
from .step3_impact import load_precomputed_impacts
from .step4_evaluate import compute_prob_exceed, load_oep_thresholds
from .step5_decide import apply_tier_rules
from .step6_output import write_outputs
from .utils import BasinRunOutput, UnitDecision, expand_template

logger = get_logger(__name__)


def evaluate_trigger_for_basin(
    basin_id: str,
    issue_date: date,
    run_spec: PipelineRunSpec,
) -> BasinRunOutput:
    """Evaluate the flood trigger for one basin using the 6-step ETL.

    Automatically selects between two impact-cube sources:

    * **Prototype mode** (``inputs.precomputed_impacts_template`` is set):
      Load a pre-built impact cube JSON from disk.
    * **Detection mode** (template absent, ``detection.*`` paths configured):
      Run the full NB07 algorithm via ``step2_detect.detect_flood_events``.
    """
    logger.info("Starting evaluation — basin='%s', issue_date=%s", basin_id, issue_date)

    if run_spec.inputs is None:
        raise ValueError("Run spec must define inputs.oep_json")

    # Step 1 — Ingest
    forecast_path = resolve_forecast_path(run_spec, issue_date, basin_id)

    # Step 4a — Load OEP thresholds
    oep_path = Path(expand_template(run_spec.inputs.oep_json, issue_date, basin_id))
    thresholds = load_oep_thresholds(oep_path, run_spec.decision.oep_min)

    # Step 2/3 — Obtain impact cube
    use_precomputed = bool(run_spec.inputs.precomputed_impacts_template)
    impacts_source: str

    if use_precomputed:
        # Prototype mode: read precomputed impact cube
        impacts_path = Path(
            expand_template(run_spec.inputs.precomputed_impacts_template, issue_date, basin_id)  # type: ignore[arg-type]
        )
        if not impacts_path.exists():
            raise FileNotFoundError(
                f"Precomputed impacts not found for basin '{basin_id}': {impacts_path}"
            )
        members, _, impact_cube = load_precomputed_impacts(impacts_path)
        impacts_source = str(impacts_path)
        logger.info("Using precomputed impact cube: %s", impacts_path)
    else:
        # Detection mode: run NB07 spatial flood detection
        if not forecast_path:
            raise FileNotFoundError(
                f"Forecast GRIB not available for basin '{basin_id}' on {issue_date}. "
                "Either supply a GRIB via ingest settings or set "
                "inputs.precomputed_impacts_template for prototype mode."
            )
        det = run_spec.detection
        evt_parquet = Path(expand_template(det.evt_params_parquet, issue_date, basin_id))
        impact_cube, members, _ = detect_flood_events(
            forecast_path=forecast_path,
            evt_params_path=evt_parquet,
            oep_json_path=oep_path,
            issue_date=issue_date,
            basin_id=basin_id,
            settings=det,
        )
        impacts_source = f"step2_detect:{forecast_path}"
        logger.info("Detection mode complete — impact cube has %d units", len(impact_cube))

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
        "impacts_source": impacts_source,
        "detection_mode": "precomputed" if use_precomputed else "step2_detect",
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
