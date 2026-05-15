"""CLI entry point for flood-ops (console script: ``flood-ops-daily``)."""

from __future__ import annotations

import argparse
from datetime import date
import sys
from typing import List

from flood_ops.etl.step0_input_evaluation import run_daily_monitoring_etl
from flood_ops.logging import get_logger

logger = get_logger(__name__)


def main(
    issue_date: str = "",
    basin_files: List[str] | None = None,
    run_spec: str = "",
) -> int:
    """Callable from both the console-script entry point and direct imports."""
    if not issue_date or basin_files is None or not run_spec:
        # Called with no args — parse from sys.argv
        parser = argparse.ArgumentParser(
            description="Run daily ETL flood trigger for specified basins",
        )
        parser.add_argument("--date", required=True, help="Issue date (YYYY-MM-DD)")
        parser.add_argument(
            "--run-spec", required=True, help="Path to ETL run-spec YAML"
        )
        parser.add_argument(
            "--basins", required=True, nargs="+", help="One or more basin YAML config files"
        )
        args = parser.parse_args()
        issue_date = args.date
        basin_files = args.basins
        run_spec = args.run_spec

    try:
        issue = date.fromisoformat(issue_date)
    except ValueError:
        logger.error("Invalid --date value '%s'. Use YYYY-MM-DD.", issue_date)
        return 1

    try:
        results, output_file = run_daily_monitoring_etl(
            issue_date=issue,
            basin_config_files=basin_files,
            run_spec_path=run_spec,
        )
    except Exception as exc:
        logger.exception("Daily ETL run failed: %s", exc)
        return 1

    total_units = sum(len(b.units) for b in results)
    total_fired = sum(
        1
        for basin in results
        for unit in basin.units
        for tier in unit.tiers
        if tier.fired
    )
    logger.info(
        "Daily ETL complete: %d basins, %d units, %d fired tier decisions. Output: %s",
        len(results),
        total_units,
        total_fired,
        output_file,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
