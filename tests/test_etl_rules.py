"""Tests for ETL rule evaluation — persistence and minimum-lead logic."""

from river_flood_monitoring.etl.forecast import _find_latest_persistent_lead


def test_latest_persistent_lead_uses_max_qualifying_lead() -> None:
    firing = [3, 4, 6, 7]
    lead = _find_latest_persistent_lead(firing_leads=firing, min_lead=3, persist_days=2)
    assert lead == 7


def test_latest_persistent_lead_respects_min_lead() -> None:
    firing = [2, 3, 4]
    lead = _find_latest_persistent_lead(firing_leads=firing, min_lead=5, persist_days=2)
    assert lead is None


def test_persistence_window_three_days() -> None:
    firing = [5, 6, 7, 9]
    lead = _find_latest_persistent_lead(firing_leads=firing, min_lead=5, persist_days=3)
    assert lead == 7


def test_persist_one_day_degrades_to_latest_single() -> None:
    firing = [4, 8]
    lead = _find_latest_persistent_lead(firing_leads=firing, min_lead=5, persist_days=1)
    assert lead == 8
