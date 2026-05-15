#!/usr/bin/env python3
"""Run the daily ETL flood monitoring pipeline for one issue date.

Usage::

    # With uv (recommended)
    uv run python ops/pipeline/run_daily_monitoring_etl.py \\
        --date 2026-05-15 \\
        --run-spec ops/configs/run_specs/daily_monitoring_etl.template.yaml \\
        --basins ops/configs/basins/Cagayan_01.yaml

    # Or via the installed console script
    flood-ops-daily --date 2026-05-15 \\
        --run-spec ops/configs/run_specs/daily_monitoring_etl.template.yaml \\
        --basins ops/configs/basins/Cagayan_01.yaml
"""

import sys
from flood_ops.cli import main

if __name__ == "__main__":
    sys.exit(main())
