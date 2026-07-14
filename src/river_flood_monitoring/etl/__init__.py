"""Daily flood monitoring ETL pipeline — public package exports.

Module layout
-------------
pipeline.py               Orchestration entry points for daily ETL runs.
run_spec.py               PipelineRunSpec settings dataclasses and YAML loader.
extract.py                Basin input extraction and threshold loading.
forecast.py               Detection, impact evaluation, and tier decision logic.
save.py                   Output serialisation for downstream systems.
utils.py                  Shared dataclasses and path-template helper.
extract-example.py        Example extraction helpers for experimentation.

Public API
----------
>>> from river_flood_monitoring.etl import run_daily_monitoring
>>> results, out_file = run_daily_monitoring(issue_date, basin_names, run_spec_path)
"""

from .run_spec import PipelineRunSpec, load_run_spec
from .pipeline import run_daily_monitoring
from .utils import BasinRunOutput, UnitDecision

__all__ = [
    "run_daily_monitoring",
    "load_run_spec",
    "PipelineRunSpec",
    "BasinRunOutput",
    "UnitDecision",
]
