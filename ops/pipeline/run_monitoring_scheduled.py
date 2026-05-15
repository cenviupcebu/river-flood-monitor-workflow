#!/usr/bin/env python3
"""Scheduled runner — fires run_daily_monitoring_etl.py once per day.

For production, replace the sleep loop with a proper scheduler
(cron, Windows Task Scheduler, Azure ML schedule, Airflow DAG).
"""

import sys
import time
from datetime import date
from pathlib import Path
import subprocess


def schedule_monitoring(
    basin_dir: str,
    run_spec: str,
    interval_hours: float = 24.0,
) -> None:
    """Run monitoring at a fixed interval.

    Parameters
    ----------
    basin_dir:
        Directory containing basin YAML config files.
    run_spec:
        Path to the ETL run-spec YAML.
    interval_hours:
        Sleep time between runs (default 24 h).
    """
    while True:
        today = date.today().isoformat()
        basin_files = list(Path(basin_dir).glob("*.yaml"))
        if not basin_files:
            print(f"No basin configs found in {basin_dir}")
        else:
            subprocess.run(
                [
                    sys.executable,
                    "ops/pipeline/run_daily_monitoring_etl.py",
                    "--date", today,
                    "--run-spec", run_spec,
                    "--basins", *[str(b) for b in basin_files],
                ],
                check=False,
            )
        time.sleep(interval_hours * 3600)


if __name__ == "__main__":
    schedule_monitoring(
        basin_dir="config/basins",
        run_spec="config/run_specs/daily_monitoring_etl.template.yaml",
        interval_hours=24.0,
    )
