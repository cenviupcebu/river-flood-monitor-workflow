"""Shared dataclasses and helper utilities for the ETL pipeline.

Imported by all step modules to avoid circular dependencies.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any, Dict, List, Optional

DEFAULT_RULE_TIERS: Dict[str, Dict[str, Any]] = {
    "T1": {"rp": 2, "p_thr": 0.50, "n_req": 1, "label": "Moderate Watch"},
    "T2": {"rp": 5, "p_thr": 0.50, "n_req": 1, "label": "High Alert"},
    "T3": {"rp": 10, "p_thr": 0.35, "n_req": 1, "label": "Very High Activation"},
}


def expand_template(template: str, issue_date: date, basin_id: Optional[str] = None) -> str:
    """Substitute {date}, {yyyy}, {mm}, {dd}, {basin_id} placeholders in a path template."""
    return template.format(
        date=issue_date.isoformat(),
        yyyy=issue_date.strftime("%Y"),
        mm=issue_date.strftime("%m"),
        dd=issue_date.strftime("%d"),
        basin_id=basin_id or "",
    )


@dataclass
class TierDecision:
    """Tier result for one administrative unit."""

    tier: str
    rp: int
    p_threshold: float
    fired: bool
    fire_lead: Optional[int]
    probability_at_fire: Optional[float]
    impact_threshold_people: Optional[float]


@dataclass
class UnitDecision:
    """Per-unit trigger decision payload."""

    unit_id: str
    tiers: List[TierDecision]


@dataclass
class BasinRunOutput:
    """Result payload for one basin for one issue date."""

    basin_id: str
    issue_date: str
    forecast_path: Optional[str]
    units: List[UnitDecision]
    metadata: Dict[str, Any]
