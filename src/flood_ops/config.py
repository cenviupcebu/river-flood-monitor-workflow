"""Minimal basin-config loader for standalone ETL runs."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass(frozen=True)
class BasinConfig:
    """Minimal basin metadata needed by the ETL orchestration."""

    basin_id: str


def load_basin_config(path: str) -> BasinConfig:
    """Load one basin YAML file and return minimal ETL config fields."""
    config_path = Path(path)
    if not config_path.exists():
        raise FileNotFoundError(f"Basin config not found: {config_path}")

    raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    basin_id = str(raw.get("basin_id", "")).strip()
    if not basin_id:
        raise ValueError(f"Missing required 'basin_id' in {config_path}")

    return BasinConfig(basin_id=basin_id)