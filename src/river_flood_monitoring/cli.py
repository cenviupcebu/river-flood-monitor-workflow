"""CLI entry point for flood-ops (console script: ``flood-monitoring``)."""

from __future__ import annotations

import argparse
from datetime import date
import sys
from typing import List

from river_flood_monitoring.config import normalize_basin_names, ALLOWED_BASINS
from river_flood_monitoring.etl.pipeline import run_daily_monitoring
from river_flood_monitoring.logging import get_logger

logger = get_logger(__name__)


def main(
    issue_date: str = "",
    basin_names: List[str] | None = None,
    run_spec: str = "",
    do_extract: bool = False,
    do_forecast: bool = False,
    do_save: bool = False,
) -> int:
    """Callable from both the console-script entry point and direct imports."""
    if not issue_date or basin_names is None or not run_spec:
        # Called with no args — parse from sys.argv
        parser = argparse.ArgumentParser(
            description="Run daily ETL flood trigger for specified basins",
        )
        parser.add_argument(
            "--run-spec", required=True, help="Path to ETL run-spec YAML"
        )
        parser.add_argument(
            "--basins", required=True, nargs="+", help=f"One or more basin names. Allowed: {', '.join(ALLOWED_BASINS)}"
        )
        parser.add_argument("--date", required=False, default=date.today().isoformat(), help="Issue date (YYYY-MM-DD)")
        parser.add_argument("--extract", action="store_true", help="Run extract step")
        parser.add_argument("--forecast", action="store_true", help="Run forecast step")
        parser.add_argument("--save", action="store_true", help="Run save step")
        args = parser.parse_args()
        issue_date = args.date
        basin_names = normalize_basin_names(args.basins)
        run_spec = args.run_spec
        do_extract = bool(args.extract)
        do_forecast = bool(args.forecast)
        do_save = bool(args.save)
    else:
        basin_names = normalize_basin_names(basin_names)

    try:
        issue = date.fromisoformat(issue_date)
    except ValueError:
        logger.error("Invalid --date value '%s'. Use YYYY-MM-DD.", issue_date)
        return 1

    try:
        results, output_file = run_daily_monitoring(
            issue_date=issue,
            basin_names=basin_names,
            run_spec_path=run_spec,
            do_extract=do_extract,
            do_forecast=do_forecast,
            do_save=do_save,
        )
    except Exception as exc:
        logger.exception("Daily ETL run failed: %s", exc)
        return 1

    selected_any = any([do_extract, do_forecast, do_save])
    if not selected_any or do_save:
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
    else:
        logger.info(
            "Daily ETL complete without save output. Steps run: "
            "extract=%s forecast=%s save=%s",
            do_extract,
            do_forecast,
            do_save,
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
