"""Daily flood monitoring ETL pipeline — public package exports.

Module layout
-------------
pipeline.py               Orchestration entry points for daily ETL runs.
pipeline_step_flags.py    Optional step-level execution flags for selective runs.
run_spec.py               PipelineRunSpec settings dataclasses and YAML loader.
prepare.py                Input preparation helpers (forecast acquisition scaffolding).
extract.py                Basin input extraction and threshold loading.
forecast.py               Detection, impact evaluation, and tier decision logic.
save.py                   Output serialisation for downstream systems.
utils.py                  Shared dataclasses and path-template helper.
extract-example.py        Example extraction helpers for experimentation.

Public API
----------
>>> from flood_ops.etl import run_daily_monitoring_etl
>>> results, out_file = run_daily_monitoring_etl(issue_date, basin_files, run_spec_path)
"""

from .run_spec import PipelineRunSpec, load_run_spec
from .pipeline import run_daily_monitoring_etl
from .utils import BasinRunOutput, UnitDecision

__all__ = [
    "run_daily_monitoring_etl",
    "load_run_spec",
    "PipelineRunSpec",
    "BasinRunOutput",
    "UnitDecision",
]
