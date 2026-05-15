"""Step 2 — Detect: spatial flood event detection from a GloFAS ensemble.

This module is a stub for the NB07 detection algorithm.  Once implemented
it will:

1. Load EVT1 GPD fits from ``evt_pot_calibration.parquet`` (NB01 output).
2. Compute per-cell return periods from ensemble discharge.
3. Run 8-neighbour connected-component labelling to identify event patches
   exceeding ``T0_YEARS=2.0`` and ``A_MIN_KM2=100``.
4. Return a per-member / per-lead event catalogue suitable for impact
   computation in Step 3.

In the current prototype the pipeline bypasses this step and reads
precomputed impact cubes directly via ``step3_impact.load_precomputed_impacts``.

Reference
---------
NB07 ``07_Trigger_Validation_Reforecast.ipynb`` — cells 3–10 contain the
reference implementation to extract into this module.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Optional

from flood_ops.logging import get_logger

logger = get_logger(__name__)


def detect_flood_events(
    forecast_path: Optional[str],
    evt_params_path: Path,
    issue_date: date,
    basin_id: str,
) -> None:
    """Run the NB07 spatial flood-event detection algorithm.

    Raises
    ------
    NotImplementedError
        Always raised until the NB07 algorithm is extracted here.  Use
        ``step3_impact.load_precomputed_impacts`` in prototype mode instead.
    """
    logger.warning(
        "Step 2 (detect_flood_events) is not yet implemented for basin '%s'. "
        "Set inputs.precomputed_impacts_template in the run-spec to use "
        "prototype mode (precomputed impact cube).",
        basin_id,
    )
    raise NotImplementedError(
        "Step 2 spatial detection is a v1.0 feature. "
        "Provide precomputed_impacts_template in the run-spec for prototype runs."
    )
