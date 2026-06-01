"""Pipeline orchestration for daily flood monitoring ETL.

This module contains the high-level pipeline entry points.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Tuple

from flood_ops.config import load_basin_config
from flood_ops.logging import get_logger, setup_pipeline_file_log

from .run_spec import load_run_spec
from .prepare import prepare
from .extract import extract
from .forecast import forecast
from .save import save
from .utils import BasinRunOutput, expand_template

logger = get_logger(__name__)


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

    # Download forecast from GloFAS
    prepare()

    basin_forecasts: List[Dict[str, Any]] = []
    for cfg_path in basin_config_files:
        cfg = load_basin_config(cfg_path)
        extracted = extract(
            config=cfg,
            issue_date=issue_date,
            run_spec=run_spec,
        )
        forecasted = forecast(extracted, issue_date, run_spec)
        basin_forecasts.append(forecasted)

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
    "forecast",
    "save",
    "run_daily_monitoring_etl",
]