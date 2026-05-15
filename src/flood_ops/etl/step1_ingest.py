"""Step 1 — Ingest: resolve or download the GloFAS ensemble forecast file."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Optional

from flood_ops.logging import get_logger
from .run_spec import PipelineRunSpec
from .utils import expand_template

logger = get_logger(__name__)


def resolve_forecast_path(
    run_spec: PipelineRunSpec,
    issue_date: date,
    basin_id: str,
) -> Optional[str]:
    """Return the local path to the GloFAS ensemble GRIB file.

    The path is derived from the run-spec template. If the file is absent
    and ``download_if_missing`` is True a warning is logged (actual download
    is reserved for a future implementation).

    Returns ``None`` when no ingest settings are defined or the file cannot
    be located.
    """
    if run_spec.ingest is None:
        logger.debug("No ingest settings — skipping forecast path resolution")
        return None

    candidate = Path(
        expand_template(run_spec.ingest.forecast_path_template, issue_date, basin_id)
    )
    logger.info("Checking forecast file: %s", candidate)

    if candidate.exists():
        logger.info("Forecast file found: %s", candidate)
        return str(candidate)

    logger.warning("Forecast file not found: %s", candidate)
    if run_spec.ingest.download_if_missing:
        logger.warning(
            "download_if_missing=True but automatic download is not yet implemented. "
            "Continuing without forecast."
        )
    return None
