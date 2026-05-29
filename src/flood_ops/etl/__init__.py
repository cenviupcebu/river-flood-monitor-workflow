"""Daily flood monitoring ETL pipeline — public package exports.

Module layout
-------------
pipeline.py               Orchestration entry points for daily ETL runs.
step0_input_evaluation.py Backward-compatible imports for orchestration.
utils.py                  Shared dataclasses and path-template helper.
run_spec.py               PipelineRunSpec settings dataclasses and YAML loader.
step1_ingest.py           Resolve or download the GloFAS ensemble forecast file.
step2_detect.py           Spatial flood-event detection from GRIB (NB07 algorithm).
step3_impact.py           Load or compute population affected per member/lead/unit.
step4_evaluate.py         Compute ensemble probability of exceeding OEP thresholds.
step5_decide.py           Apply tier rules with persistence and minimum-lead constraints.
step6_output.py           Serialise trigger decisions for downstream systems.

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
