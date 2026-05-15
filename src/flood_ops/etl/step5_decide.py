"""Step 5 — Decide: apply tier rules with persistence and minimum-lead constraints."""

from __future__ import annotations

from typing import Dict, Iterable, List, Optional

from flood_ops.logging import get_logger
from .run_spec import DecisionSettings
from .utils import TierDecision, UnitDecision

logger = get_logger(__name__)


def find_latest_persistent_lead(
    firing_leads: Iterable[int],
    min_lead: int,
    persist_days: int,
) -> Optional[int]:
    """Return the latest lead day satisfying both persistence and minimum lead.

    A lead qualifies when it is part of a contiguous window of
    ``persist_days`` consecutive integer lead days all of which appear in
    ``firing_leads``, and the lead is >= ``min_lead``.

    Returns the latest (highest) such lead for maximum warning time.
    """
    if persist_days <= 1:
        eligible = [ld for ld in firing_leads if ld >= min_lead]
        return max(eligible) if eligible else None

    firing_set = set(int(ld) for ld in firing_leads)
    eligible = sorted([ld for ld in firing_set if ld >= min_lead], reverse=True)

    for lead in eligible:
        for start in range(lead - persist_days + 1, lead + 1):
            window = {start + step for step in range(persist_days)}
            if lead in window and window.issubset(firing_set):
                return lead
    return None


def apply_tier_rules(
    prob_exceed: Dict[str, Dict[int, Dict[int, float]]],
    thresholds: Dict[str, Dict[int, float]],
    decision: DecisionSettings,
) -> List[UnitDecision]:
    """Evaluate all tier rules across all units.

    Parameters
    ----------
    prob_exceed:
        Exceedance probability cube — unit → lead → rp → probability.
    thresholds:
        OEP thresholds — unit → rp → impact_threshold_people.
    decision:
        Policy settings (persist_days, min_lead, rule list).

    Returns
    -------
    list of UnitDecision
    """
    logger.info(
        "Applying %d tier rules to %d units (persist_days=%d, min_lead=%d)",
        len(decision.rules),
        len(prob_exceed),
        decision.persist_days,
        decision.min_lead,
    )
    units: List[UnitDecision] = []
    fired_count = 0

    for unit_id, lead_map in prob_exceed.items():
        tier_results: List[TierDecision] = []
        for rule in decision.rules:
            firing = [
                lead
                for lead, rp_prob in lead_map.items()
                if rp_prob.get(rule.rp, 0.0) >= rule.p_thr
            ]
            fire_lead = find_latest_persistent_lead(
                firing,
                min_lead=decision.min_lead,
                persist_days=decision.persist_days,
            )
            prob_at_fire: Optional[float] = None
            if fire_lead is not None:
                prob_at_fire = lead_map.get(fire_lead, {}).get(rule.rp)
                fired_count += 1
                logger.info(
                    "Tier %s FIRED — unit='%s', fire_lead=%d, p=%.2f",
                    rule.name,
                    unit_id,
                    fire_lead,
                    prob_at_fire or 0.0,
                )

            tier_results.append(
                TierDecision(
                    tier=rule.name,
                    rp=rule.rp,
                    p_threshold=rule.p_thr,
                    fired=fire_lead is not None,
                    fire_lead=fire_lead,
                    probability_at_fire=prob_at_fire,
                    impact_threshold_people=thresholds.get(unit_id, {}).get(rule.rp),
                )
            )
        units.append(UnitDecision(unit_id=unit_id, tiers=tier_results))

    logger.info(
        "Tier evaluation complete: %d units, %d tier decisions fired",
        len(units),
        fired_count,
    )
    return units
