"""Extract stage for daily flood monitoring ETL.

This module prepares basin-level inputs for forecasting by resolving forecast
NetCDF file paths and loading OEP thresholds from configured sources.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional
import json
import re

from flood_ops.config import BasinConfig
from flood_ops.logging import get_logger
from .run_spec import PipelineRunSpec
from .utils import expand_template

logger = get_logger(__name__)


def extract(
    config: BasinConfig,
    issue_date: date,
    run_spec: PipelineRunSpec,
) -> Dict[str, Any]:
    """
    Prepare basin inputs required for the forecast step.
    """
    basin_name = config.basin_name
    logger.info("Processing basin '%s'", basin_name)

    if run_spec.inputs is None:
        raise ValueError("Run spec must define inputs.oep_json")

    # load GloFAS file paths (single file or ensemble-member files)
    forecast_paths = _resolve_forecast_path(run_spec, issue_date)
    if not forecast_paths:
        raise FileNotFoundError(
            f"Forecast file not available for basin '{basin_name}' on {issue_date}. "
            "Supply a forecast file via ingest settings."
        )

    # load EVT parameters Parquet path
    evt_template = str(run_spec.inputs.evt_params_parquet).strip()
    evt_parquet = Path(expand_template(evt_template, issue_date, basin=basin_name))
    # load OEP file path
    oep_template = str(run_spec.inputs.oep_json).strip()
    oep_path = Path(expand_template(oep_template, issue_date, basin=basin_name))
    thresholds, unit_metadata = _load_oep_thresholds(oep_path, run_spec.decision.oep_min)

    det = run_spec.detection

    return {
        "basin_name": basin_name,
        "forecast_paths": forecast_paths,
        "oep_path": oep_path,
        "thresholds": thresholds,
        "unit_metadata": unit_metadata,
        "evt_parquet": evt_parquet,
        "det": det,
    }


def _resolve_forecast_path(
    run_spec: PipelineRunSpec,
    issue_date: date,
) -> Optional[List[str]]:
    """
    Return local path(s) to the GloFAS ensemble NetCDF forecast files.

    The path is derived from ``forecast_path_template``. If the template
    contains ``{ens}`` or ``{ens_no}``, it is treated as a glob pattern and
    all matching files are returned in sorted order.

    Returns ``None`` when no ingest settings are defined or no files are found.
    """
    if run_spec.ingest is None:
        logger.debug("No ingest settings — skipping forecast path resolution")
        return None

    template = run_spec.ingest.forecast_path_template
    has_ens_placeholder = "{ens}" in template or "{ens_no}" in template
    if has_ens_placeholder:
        template_for_lookup = template.replace("{ens}", "*").replace("{ens_no}", "*")
    else:
        # Support templates using a fixed member token like dis_00_YYYYMMDD00.nc.
        template_for_lookup = re.sub(r"([_-])00(?=[_-])", r"\1*", template, count=1)

    candidate = Path(expand_template(template_for_lookup, issue_date))

    if has_ens_placeholder or template_for_lookup != template:
        logger.info("Checking forecast file pattern: %s", candidate)
        matched = sorted(p for p in candidate.parent.glob(candidate.name) if p.is_file())
        if matched:
            logger.info("Forecast files found: %d match(es)", len(matched))
            return [str(p) for p in matched]
    else:
        logger.info("Checking forecast file: %s", candidate)
        if candidate.exists():
            logger.info("Forecast file found: %s", candidate)
            return [str(candidate)]

    logger.warning("Forecast file(s) not found: %s", candidate)
    if run_spec.ingest.download_if_missing:
        raise NotImplementedError(
            "download_if_missing=True but automatic download is not yet implemented. "
            "Continuing without forecast."
        )
    return None


def _load_oep_thresholds(
    oep_json_path: Path,
    oep_min: float,
) -> tuple[Dict[str, Dict[int, float]], Dict[str, Dict[str, str]]]:
    """
    Load per-unit OEP impact thresholds from JSON.

    Units whose RP2 threshold is below ``oep_min`` are excluded.

    Returns
    -------
    tuple
        ``({unit_id: {rp: threshold_people}}, {unit_id: {level, name, pcode}})``
    """
    logger.info(
        "Loading OEP thresholds from %s (oep_min=%.0f)", oep_json_path, oep_min
    )
    if not oep_json_path.exists():
        raise FileNotFoundError(f"OEP JSON not found: {oep_json_path}")

    raw = json.loads(oep_json_path.read_text(encoding="utf-8"))
    rp_report = [int(float(x)) for x in raw.get("rp_report", [])]

    thresholds: Dict[str, Dict[int, float]] = {}
    unit_metadata: Dict[str, Dict[str, str]] = {}
    for rec in raw.get("units", []):
        pcode = rec.get("pcode")
        if not pcode:
            continue
        unit_id = f"ADM3::{str(pcode)}"
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
            thresholds[unit_id] = rp_map
            unit_metadata[unit_id] = {
                "level": str(rec.get("level", "") or "").strip(),
                "name": str(rec.get("name", "") or "").strip(),
                "pcode": str(pcode).strip(),
            }

    logger.info(
        "OEP thresholds loaded: %d qualifying units (from %d total, oep_min=%.0f)",
        len(thresholds),
        len(raw.get("units", [])),
        oep_min,
    )
    return thresholds, unit_metadata