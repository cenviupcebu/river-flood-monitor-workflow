"""Pipeline orchestration for daily flood monitoring ETL.

This module contains the high-level pipeline entry points.
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import date
from datetime import datetime
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from flood_ops.config import load_basin_config
from flood_ops.logging import get_logger, setup_pipeline_file_log

from .run_spec import load_run_spec
from .prepare import prepare
from .extract import extract
from .forecast import forecast
from .save import save
from .run_spec import DetectionSettings
from .utils import BasinRunOutput, TierDecision, UnitDecision, expand_template

logger = get_logger(__name__)


def run_daily_monitoring_etl(
    issue_date: date,
    basin_config_files: List[str],
    run_spec_path: str,
    do_prepare: bool = False,
    do_extract: bool = False,
    do_forecast: bool = False,
    do_save: bool = False,
) -> Tuple[List[BasinRunOutput], Optional[Path]]:
    """Execute selected ETL steps for all requested basins.

    Attaches a dated ``logs/<run_name>_<datetime>.txt`` file handler before
    the first basin so the full run trace is persisted.

    If no step flags are enabled, all steps run in order. When an upstream
    step is skipped, this function attempts to load the required artifacts
    from ``data/etl_step_cache/<run_name>/<issue_date>/``.
    """
    if not any([do_prepare, do_extract, do_forecast, do_save]):
        do_prepare = True
        do_extract = True
        do_forecast = True
        do_save = True

    run_spec = load_run_spec(run_spec_path)

    # TODO: use logs directory from run_spec.output when available
    log_dir_template = "logs"
    if run_spec.output is not None:
        log_dir_template = getattr(run_spec.output, "log_dir_template", "logs")
    log_file = setup_pipeline_file_log(
        log_dir=Path(expand_template(log_dir_template, issue_date)),
        run_name=run_spec.run_name,
    )

    logger.info(
        "run_daily_monitoring_etl started — run='%s', issue_date=%s, basins=%d, "
        "steps=[prepare=%s, extract=%s, forecast=%s, save=%s], log=%s",
        run_spec.run_name,
        issue_date,
        len(basin_config_files),
        do_prepare,
        do_extract,
        do_forecast,
        do_save,
        log_file,
    )

    artifact_root = _artifact_root(run_spec.run_name, issue_date)
    artifact_root.mkdir(parents=True, exist_ok=True)

    _write_run_manifest(
        artifact_root=artifact_root,
        run_name=run_spec.run_name,
        issue_date=issue_date,
        basin_config_files=basin_config_files,
        run_spec_path=run_spec_path,
        selected_steps={
            "prepare": do_prepare,
            "extract": do_extract,
            "forecast": do_forecast,
            "save": do_save,
        },
    )

    if do_prepare:
        prepare()
        _write_prepare_marker(artifact_root)
    elif any([do_extract, do_forecast, do_save]):
        marker = _prepare_marker_path(artifact_root)
        if not marker.exists():
            logger.warning(
                "Prepare marker not found at %s. Continuing without prepare step.",
                marker,
            )

    extracted_by_basin: Dict[str, Dict[str, Any]] = {}
    forecast_by_basin: Dict[str, Dict[str, Any]] = {}

    for cfg_path in basin_config_files:
        cfg = load_basin_config(cfg_path)
        basin_id = cfg.basin_id

        if do_extract:
            extracted = extract(
                config=cfg,
                issue_date=issue_date,
                run_spec=run_spec,
            )
            _write_extract_artifact(artifact_root, basin_id, extracted)
            extracted_by_basin[basin_id] = extracted
        elif do_forecast:
            extracted_by_basin[basin_id] = _read_extract_artifact(artifact_root, basin_id)

        if do_forecast:
            forecasted = forecast(extracted_by_basin[basin_id], issue_date, run_spec)
            _write_forecast_artifact(artifact_root, basin_id, forecasted)
            forecast_by_basin[basin_id] = forecasted
        elif do_save:
            forecast_by_basin[basin_id] = _read_forecast_artifact(artifact_root, basin_id)

    if do_save:
        basin_forecasts = [
            forecast_by_basin[load_basin_config(p).basin_id] for p in basin_config_files
        ]
        basin_results, output_file = save(run_spec, issue_date, basin_forecasts)

        total_fired = sum(
            1
            for basin in basin_results
            for unit in basin.units
            for tier in unit.tiers
            if tier.fired
        )
        logger.info(
            "run_daily_monitoring_etl complete — %d basins, %d tier fires, output=%s",
            len(basin_results),
            total_fired,
            output_file,
        )
        return basin_results, output_file

    if do_forecast:
        total_units = sum(len(v.get("units", [])) for v in forecast_by_basin.values())
        logger.info(
            "run_daily_monitoring_etl complete (no save) — forecast artifacts=%d, units=%d",
            len(forecast_by_basin),
            total_units,
        )
    elif do_extract:
        logger.info(
            "run_daily_monitoring_etl complete (extract only) — artifacts=%d",
            len(extracted_by_basin),
        )
    else:
        logger.info("run_daily_monitoring_etl complete (prepare only)")

    return [], None


def _artifact_root(run_name: str, issue_date: date) -> Path:
    return Path("data/etl_step_cache") / run_name / issue_date.isoformat()


def _run_manifest_path(artifact_root: Path) -> Path:
    return artifact_root / "run_manifest.json"


def _prepare_marker_path(artifact_root: Path) -> Path:
    return artifact_root / "prepare.done.json"


def _extract_artifact_path(artifact_root: Path, basin_id: str) -> Path:
    return artifact_root / "extract" / f"{basin_id}.json"


def _forecast_artifact_path(artifact_root: Path, basin_id: str) -> Path:
    return artifact_root / "forecast" / f"{basin_id}.json"


def _write_run_manifest(
    artifact_root: Path,
    run_name: str,
    issue_date: date,
    basin_config_files: List[str],
    run_spec_path: str,
    selected_steps: Dict[str, bool],
) -> None:
    payload = {
        "schema_version": 1,
        "run_name": run_name,
        "issue_date": issue_date.isoformat(),
        "run_spec_path": run_spec_path,
        "basin_config_files": basin_config_files,
        "selected_steps": selected_steps,
        "generated_at_utc": datetime.utcnow().isoformat() + "Z",
    }
    path = _run_manifest_path(artifact_root)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _write_prepare_marker(artifact_root: Path) -> None:
    marker = {
        "schema_version": 1,
        "status": "ok",
        "completed_at_utc": datetime.utcnow().isoformat() + "Z",
    }
    path = _prepare_marker_path(artifact_root)
    path.write_text(json.dumps(marker, indent=2), encoding="utf-8")


def _write_extract_artifact(
    artifact_root: Path,
    basin_id: str,
    extracted: Dict[str, Any],
) -> None:
    path = _extract_artifact_path(artifact_root, basin_id)
    path.parent.mkdir(parents=True, exist_ok=True)

    payload = {
        "schema_version": 1,
        "basin_id": str(extracted["basin_id"]),
        "forecast_paths": [str(p) for p in extracted.get("forecast_paths") or []],
        "forecast_filename_example": extracted.get("forecast_filename_example"),
        "oep_path": _to_optional_str(extracted.get("oep_path")),
        "thresholds": extracted.get("thresholds", {}),
        "unit_metadata": extracted.get("unit_metadata", {}),
        "evt_parquet": _to_optional_str(extracted.get("evt_parquet")),
        "det": asdict(extracted["det"]),
        "generated_at_utc": datetime.utcnow().isoformat() + "Z",
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _read_extract_artifact(artifact_root: Path, basin_id: str) -> Dict[str, Any]:
    path = _extract_artifact_path(artifact_root, basin_id)
    if not path.exists():
        raise FileNotFoundError(
            f"Extract artifact not found for basin '{basin_id}': {path}. "
            "Run with --extract first."
        )

    raw = json.loads(path.read_text(encoding="utf-8"))
    thresholds: Dict[str, Dict[int, float]] = {}
    for unit_id, rp_map in (raw.get("thresholds") or {}).items():
        thresholds[unit_id] = {int(k): float(v) for k, v in rp_map.items()}

    det_cfg = raw.get("det") or {}
    det = DetectionSettings(
        t0_years=float(det_cfg.get("t0_years", 2.0)),
        a_min_km2=float(det_cfg.get("a_min_km2", 100.0)),
        depth_threshold_m=float(det_cfg.get("depth_threshold_m", 0.02)),
        cc_connectivity=int(det_cfg.get("cc_connectivity", 2)),
        flood_detect_rps=[int(v) for v in det_cfg.get("flood_detect_rps", [2, 5, 10, 20])],
        evt_params_parquet=str(det_cfg.get("evt_params_parquet", "")),
        jrc_root=str(det_cfg.get("jrc_root", "")),
        worldpop_tif=str(det_cfg.get("worldpop_tif", "")),
        adm3_geojson=str(det_cfg.get("adm3_geojson", "")),
    )

    return {
        "basin_id": str(raw["basin_id"]),
        "forecast_paths": [str(p) for p in raw.get("forecast_paths") or []],
        "forecast_filename_example": raw.get("forecast_filename_example"),
        "oep_path": Path(raw["oep_path"]),
        "thresholds": thresholds,
        "unit_metadata": raw.get("unit_metadata") or {},
        "evt_parquet": Path(raw["evt_parquet"]),
        "det": det,
    }


def _write_forecast_artifact(
    artifact_root: Path,
    basin_id: str,
    forecasted: Dict[str, Any],
) -> None:
    path = _forecast_artifact_path(artifact_root, basin_id)
    path.parent.mkdir(parents=True, exist_ok=True)

    payload = {
        "schema_version": 1,
        "basin_id": str(forecasted["basin_id"]),
        "forecast_paths": [str(p) for p in forecasted.get("forecast_paths") or []],
        "oep_path": _to_optional_str(forecasted.get("oep_path")),
        "impacts_source": str(forecasted.get("impacts_source", "")),
        "units": [_serialise_unit(unit) for unit in forecasted.get("units") or []],
        "generated_at_utc": datetime.utcnow().isoformat() + "Z",
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _read_forecast_artifact(artifact_root: Path, basin_id: str) -> Dict[str, Any]:
    path = _forecast_artifact_path(artifact_root, basin_id)
    if not path.exists():
        raise FileNotFoundError(
            f"Forecast artifact not found for basin '{basin_id}': {path}. "
            "Run with --forecast first."
        )

    raw = json.loads(path.read_text(encoding="utf-8"))
    return {
        "basin_id": str(raw["basin_id"]),
        "forecast_paths": [str(p) for p in raw.get("forecast_paths") or []],
        "oep_path": Path(raw["oep_path"]),
        "impacts_source": str(raw.get("impacts_source", "")),
        "units": [_deserialise_unit(unit) for unit in raw.get("units") or []],
    }


def _serialise_unit(unit: UnitDecision) -> Dict[str, Any]:
    return {
        "unit_id": unit.unit_id,
        "level": unit.level,
        "name": unit.name,
        "pcode": unit.pcode,
        "tiers": [
            {
                "tier": tier.tier,
                "rp": tier.rp,
                "p_threshold": tier.p_threshold,
                "fired": tier.fired,
                "fire_lead": tier.fire_lead,
                "probability_at_fire": tier.probability_at_fire,
                "impact_threshold_people": tier.impact_threshold_people,
            }
            for tier in unit.tiers
        ],
    }


def _deserialise_unit(raw: Dict[str, Any]) -> UnitDecision:
    tiers = [
        TierDecision(
            tier=str(t["tier"]),
            rp=int(t["rp"]),
            p_threshold=float(t["p_threshold"]),
            fired=bool(t["fired"]),
            fire_lead=int(t["fire_lead"]) if t.get("fire_lead") is not None else None,
            probability_at_fire=(
                float(t["probability_at_fire"])
                if t.get("probability_at_fire") is not None
                else None
            ),
            impact_threshold_people=(
                float(t["impact_threshold_people"])
                if t.get("impact_threshold_people") is not None
                else None
            ),
        )
        for t in raw.get("tiers") or []
    ]
    return UnitDecision(
        unit_id=str(raw["unit_id"]),
        level=str(raw.get("level", "") or ""),
        name=str(raw.get("name", "") or ""),
        pcode=str(raw.get("pcode", "") or ""),
        tiers=tiers,
    )


def _to_optional_str(value: Any) -> Optional[str]:
    if value is None:
        return None
    return str(value)


__all__ = [
    "prepare",
    "extract",
    "forecast",
    "save",
    "run_daily_monitoring_etl",
]