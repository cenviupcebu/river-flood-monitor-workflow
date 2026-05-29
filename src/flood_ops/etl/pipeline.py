"""Pipeline orchestration for daily flood monitoring ETL.

This module contains the high-level pipeline entry points.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Tuple

from flood_ops.config import load_basin_config
from flood_ops.logging import get_logger, setup_pipeline_file_log

from .run_spec import PipelineRunSpec, load_run_spec
from .step1_ingest import resolve_forecast_path
from .step2_detect import detect_flood_events
from .step4_evaluate import compute_prob_exceed, load_oep_thresholds
from .step5_decide import apply_tier_rules
from .step6_output import write_outputs
from .utils import BasinRunOutput, UnitDecision, expand_template

logger = get_logger(__name__)


def prepare(
    cfg_path: str,
    issue_date: date,
    run_spec: PipelineRunSpec,
) -> Dict[str, Any]:
    """Prepare basin inputs required for the forecast step."""
    cfg = load_basin_config(cfg_path)
    basin_id = cfg.basin_id
    logger.info("Processing basin '%s' from %s", basin_id, cfg_path)

    if run_spec.inputs is None:
        raise ValueError("Run spec must define inputs.oep_json")

    forecast_path = resolve_forecast_path(run_spec, issue_date, basin_id)
    oep_path = Path(expand_template(run_spec.inputs.oep_json, issue_date, basin_id))
    thresholds = load_oep_thresholds(oep_path, run_spec.decision.oep_min)

    return {
        "basin_id": basin_id,
        "forecast_path": forecast_path,
        "oep_path": oep_path,
        "thresholds": thresholds,
    }


def extract(
    prepared: Dict[str, Any],
    issue_date: date,
    run_spec: PipelineRunSpec,
) -> Dict[str, Any]:
    """Extract detection inputs derived from prepared basin context."""
    basin_id = str(prepared["basin_id"])
    forecast_path = prepared["forecast_path"]
    if not forecast_path:
        raise FileNotFoundError(
            f"Forecast file not available for basin '{basin_id}' on {issue_date}. "
            "Either supply a forecast file via ingest settings or set "
            "inputs.precomputed_impacts_template for prototype mode."
        )

    det = run_spec.detection
    evt_parquet = Path(expand_template(det.evt_params_parquet, issue_date, basin_id))
    return {
        "basin_id": basin_id,
        "forecast_path": forecast_path,
        "oep_path": prepared["oep_path"],
        "thresholds": prepared["thresholds"],
        "evt_parquet": evt_parquet,
        "det": det,
    }


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

    prob_exceed = compute_prob_exceed(impact_cube, extracted["thresholds"], members)
    units: List[UnitDecision] = apply_tier_rules(
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

    output_file = write_outputs(run_spec, issue_date, basin_results)
    return basin_results, output_file


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

    # TODO: use logs directory from run_spec.output when available
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

    basin_forecasts: List[Dict[str, Any]] = []
    for cfg_path in basin_config_files:
        prepared = prepare(cfg_path, issue_date, run_spec)
        extracted = extract(prepared, issue_date, run_spec)
        basin_forecasts.append(forecast(extracted, issue_date, run_spec))

    basin_results, output_file = save(run_spec, issue_date, basin_forecasts)

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


__all__ = [
    "prepare",
    "extract",
    "forecast",
    "save",
    "run_daily_monitoring_etl",
]