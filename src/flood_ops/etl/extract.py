from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any, Dict, Optional
import json

from flood_ops.config import BasinConfig
from flood_ops.logging import get_logger
from .run_spec import PipelineRunSpec
from .utils import expand_template

logger = get_logger(__name__)


"""
Step 2 — Extract:
- download the GloFAS ensemble forecast files.
- load OEP thresholds from the provided OEP JSON file.
"""


def extract(
    config: BasinConfig,
    issue_date: date,
    run_spec: PipelineRunSpec,
) -> Dict[str, Any]:
    """
    Prepare basin inputs required for the forecast step.
    """
    basin_id = config.basin_id
    logger.info("Processing basin '%s'", basin_id)

    if run_spec.inputs is None:
        raise ValueError("Run spec must define inputs.oep_json")

    # load GloFAS file path
    forecast_path = _resolve_forecast_path(run_spec, issue_date, basin_id)
    if not forecast_path:
        raise FileNotFoundError(
            f"Forecast file not available for basin '{basin_id}' on {issue_date}. "
            "Either supply a forecast file via ingest settings or set "
            "inputs.precomputed_impacts_template for prototype mode."
        )

    det = run_spec.detection
    evt_parquet = Path(expand_template(det.evt_params_parquet, issue_date, basin_id))

    # load OEP file path
    oep_path = Path(expand_template(run_spec.inputs.oep_json, issue_date, basin_id))
    thresholds = _load_oep_thresholds(oep_path, run_spec.decision.oep_min)

    return {
        "basin_id": basin_id,
        "forecast_path": forecast_path,
        "oep_path": oep_path,
        "thresholds": thresholds,
        "evt_parquet": evt_parquet,
        "det": det,
    }


def _resolve_forecast_path(
    run_spec: PipelineRunSpec,
    issue_date: date,
    basin_id: str,
) -> Optional[str]:
    """
    Return the local path to the GloFAS ensemble GRIB file.

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
        raise NotImplementedError(
            "download_if_missing=True but automatic download is not yet implemented. "
            "Continuing without forecast."
        )


def _load_oep_thresholds(
    oep_json_path: Path,
    oep_min: float,
) -> Dict[str, Dict[int, float]]:
    """
    Load per-unit OEP impact thresholds from the NB05 JSON.

    Units whose RP2 threshold is below ``oep_min`` are excluded.

    Returns
    -------
    dict
        ``{unit_id: {rp: threshold_people}}``
    """
    logger.info(
        "Loading OEP thresholds from %s (oep_min=%.0f)", oep_json_path, oep_min
    )
    if not oep_json_path.exists():
        raise FileNotFoundError(f"OEP JSON not found: {oep_json_path}")

    raw = json.loads(oep_json_path.read_text(encoding="utf-8"))
    rp_report = [int(float(x)) for x in raw.get("rp_report", [])]

    thresholds: Dict[str, Dict[int, float]] = {}
    for rec in raw.get("units", []):
        unit = rec.get("unit")
        if not unit:
            continue
        oep_rl = rec.get("oep_rl", [])
        rp_map: Dict[int, float] = {}
        for idx, rp in enumerate(rp_report):
            if idx >= len(oep_rl):
                continue
            try:
                rp_map[rp] = float(oep_rl[idx])
            except (TypeError, ValueError):
                continue
        if rp_map.get(2, 0.0) >= oep_min:
            thresholds[str(unit)] = rp_map

    logger.info(
        "OEP thresholds loaded: %d qualifying units (from %d total, oep_min=%.0f)",
        len(thresholds),
        len(raw.get("units", [])),
        oep_min,
    )
    return thresholds