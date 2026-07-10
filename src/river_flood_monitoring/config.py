"""Basin selection helpers for standalone ETL runs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import List


# Allowed basins for this workflow
ALLOWED_BASINS = ["cagayan", "bicol"]


@dataclass(frozen=True)
class BasinConfig:
    """Minimal basin metadata needed by the ETL orchestration."""

    basin_name: str


def normalize_basin_names(basin_names: List[str]) -> List[str]:
    """Normalize and validate requested basin names from CLI input."""
    normalized: List[str] = []
    for basin_name in basin_names:
        basin_name_lower = str(basin_name).strip().lower()
        if not basin_name_lower:
            continue
        if basin_name_lower not in ALLOWED_BASINS:
            raise ValueError(
                f"Basin '{basin_name}' is not allowed. "
                f"Allowed basins: {', '.join(ALLOWED_BASINS)}"
            )
        normalized.append(basin_name_lower)

    if not normalized:
        raise ValueError("At least one basin name must be provided.")

    return normalized


def build_basin_configs(basin_names: List[str]) -> List[BasinConfig]:
    """Build basin configs where basin_name is sourced from basin name."""
    normalized = normalize_basin_names(basin_names)
    return [BasinConfig(basin_name=name) for name in normalized]