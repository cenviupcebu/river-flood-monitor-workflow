"""Minimal basin-config loader for standalone ETL runs."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import List

import yaml


# Allowed basins for this workflow
ALLOWED_BASINS = ["cagayan"]


@dataclass(frozen=True)
class BasinConfig:
    """Minimal basin metadata needed by the ETL orchestration."""

    basin_id: str


def resolve_basin_names_to_paths(basin_names: List[str], basins_dir: str = "config/basins") -> List[str]:
    """Resolve basin names to file paths.
    
    Args:
        basin_names: List of basin names (e.g., ["cagayan", "abra"])
        basins_dir: Directory containing basin config files
        
    Returns:
        List of resolved file paths (e.g., ["config/basins/cagayan_basin.yaml", ...])
        
    Raises:
        ValueError: If a basin name is not in the allowed list
        FileNotFoundError: If a basin config file cannot be found
    """
    basins_path = Path(basins_dir)
    resolved_paths = []
    
    for basin_name in basin_names:
        basin_name_lower = basin_name.lower()
        
        # Check if basin is in allowed list
        if basin_name_lower not in ALLOWED_BASINS:
            raise ValueError(
                f"Basin '{basin_name}' is not allowed. "
                f"Allowed basins: {', '.join(ALLOWED_BASINS)}"
            )
        
        # Try to find basin config file matching the pattern: {basin_name}_basin.yaml
        config_file = basins_path / f"{basin_name_lower}_basin.yaml"
        
        if not config_file.exists():
            raise FileNotFoundError(
                f"Basin config file not found for '{basin_name}': {config_file}"
            )
        
        resolved_paths.append(str(config_file))
    
    return resolved_paths


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