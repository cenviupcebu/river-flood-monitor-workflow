"""Settings dataclasses and YAML loader for the prototype ETL run-spec."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from .utils import DEFAULT_RULE_TIERS


@dataclass
class TierRule:
    """Activation parameters for one operational tier."""

    name: str
    rp: int
    p_thr: float
    n_req: int = 1
    label: str = ""


@dataclass
class DecisionSettings:
    """Policy constants that govern tier activation."""

    persist_days: int = 2
    min_lead: int = 5
    oep_min: float = 100.0
    rules: List[TierRule] = field(default_factory=list)


@dataclass
class IngestSettings:
    """Forecast file location settings for Step 1."""

    forecast_path_template: str
    download_if_missing: bool = False
    downloader_command_template: Optional[str] = None


@dataclass
class InputSettings:
    """Required data paths consumed in Steps 3-4."""

    oep_json: str
    precomputed_impacts_template: Optional[str] = None


@dataclass
class OutputSettings:
    """Output location and format for Step 6."""

    output_dir_template: str
    format: str = "json"
    log_dir_template: str = "logs"


@dataclass
class PipelineRunSpec:
    """Top-level run specification for the prototype ETL."""

    run_name: str = "daily_flood_monitoring"
    ingest: Optional[IngestSettings] = None
    inputs: Optional[InputSettings] = None
    decision: DecisionSettings = field(default_factory=DecisionSettings)
    output: Optional[OutputSettings] = None


def parse_tier_rules(raw_rules: Dict[str, Dict[str, Any]]) -> List[TierRule]:
    """Build a sorted list of TierRule objects from a raw config dict."""
    rules = [
        TierRule(
            name=str(name),
            rp=int(cfg["rp"]),
            p_thr=float(cfg["p_thr"]),
            n_req=int(cfg.get("n_req", 1)),
            label=str(cfg.get("label", name)),
        )
        for name, cfg in raw_rules.items()
    ]
    rules.sort(key=lambda r: r.rp)
    return rules


def load_run_spec(path: str) -> PipelineRunSpec:
    """Load a YAML run-spec file into typed settings objects."""
    with open(Path(path), "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    ingest_cfg = raw.get("ingest") or {}
    inputs_cfg = raw.get("inputs") or {}
    output_cfg = raw.get("output") or {}
    decision_cfg = raw.get("decision") or {}

    ingest = None
    if ingest_cfg.get("forecast_path_template"):
        ingest = IngestSettings(
            forecast_path_template=str(ingest_cfg["forecast_path_template"]),
            download_if_missing=bool(ingest_cfg.get("download_if_missing", False)),
            downloader_command_template=ingest_cfg.get("downloader_command_template"),
        )

    inputs = None
    if inputs_cfg.get("oep_json"):
        inputs = InputSettings(
            oep_json=str(inputs_cfg["oep_json"]),
            precomputed_impacts_template=inputs_cfg.get("precomputed_impacts_template"),
        )

    output = None
    if output_cfg.get("output_dir_template"):
        output = OutputSettings(
            output_dir_template=str(output_cfg["output_dir_template"]),
            format=str(output_cfg.get("format", "json")).lower(),
            log_dir_template=str(output_cfg.get("log_dir_template", "logs")),
        )

    rules_raw = decision_cfg.get("rule_tiers") or DEFAULT_RULE_TIERS
    decision = DecisionSettings(
        persist_days=int(decision_cfg.get("persist_days", 2)),
        min_lead=int(decision_cfg.get("min_lead", 5)),
        oep_min=float(decision_cfg.get("oep_min", 100.0)),
        rules=parse_tier_rules(rules_raw),
    )

    return PipelineRunSpec(
        run_name=str(raw.get("run_name", "daily_flood_monitoring")),
        ingest=ingest,
        inputs=inputs,
        decision=decision,
        output=output,
    )
