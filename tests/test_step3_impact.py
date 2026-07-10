"""Tests for Step 3 impact aggregation from detected event patches."""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

from river_flood_monitoring.etl.step3_impact import EventPatchImpactInput, compute_impacts_from_event_patches


def _install_fake_philflood(monkeypatch: pytest.MonkeyPatch, func) -> None:
    """Install a fake philflood module tree in sys.modules for testing."""
    philflood = types.ModuleType("philflood")
    models = types.ModuleType("philflood.models")
    impact = types.ModuleType("philflood.models.impact")
    population_exposure = types.ModuleType("philflood.models.impact.population_exposure")
    population_exposure.aggregate_affected_population = func

    philflood.models = models
    models.impact = impact
    impact.population_exposure = population_exposure

    monkeypatch.setitem(sys.modules, "philflood", philflood)
    monkeypatch.setitem(sys.modules, "philflood.models", models)
    monkeypatch.setitem(sys.modules, "philflood.models.impact", impact)
    monkeypatch.setitem(
        sys.modules,
        "philflood.models.impact.population_exposure",
        population_exposure,
    )


def test_compute_impacts_from_event_patches_accumulates_per_unit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = []

    def fake_aggregate_affected_population(**kwargs):
        calls.append(kwargs)
        return {"TownA": 10.0, "ADM3::TownB": 3.0}

    _install_fake_philflood(monkeypatch, fake_aggregate_affected_population)

    worldpop = tmp_path / "worldpop.tif"
    worldpop.write_bytes(b"fake")
    depth1 = tmp_path / "depth_1.tif"
    depth1.write_bytes(b"fake")
    depth2 = tmp_path / "depth_2.tif"
    depth2.write_bytes(b"fake")

    patches = [
        EventPatchImpactInput(lead_day=5, member_id=1, rp=2, depth_raster=depth1, event_id="p1"),
        EventPatchImpactInput(lead_day=5, member_id=1, rp=2, depth_raster=depth2, event_id="p2"),
    ]

    members, leads, cube = compute_impacts_from_event_patches(
        patches=patches,
        worldpop_tif=worldpop,
        depth_threshold_m=0.02,
    )

    assert members == [1]
    assert leads == [5]
    assert cube["ADM3::TownA"][5][1][2] == 20.0
    assert cube["ADM3::TownB"][5][1][2] == 6.0
    assert len(calls) == 2


def test_compute_impacts_from_event_patches_requires_philflood(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delitem(sys.modules, "philflood", raising=False)
    monkeypatch.delitem(sys.modules, "philflood.models", raising=False)
    monkeypatch.delitem(sys.modules, "philflood.models.impact", raising=False)
    monkeypatch.delitem(
        sys.modules,
        "philflood.models.impact.population_exposure",
        raising=False,
    )

    worldpop = tmp_path / "worldpop.tif"
    worldpop.write_bytes(b"fake")
    depth = tmp_path / "depth_1.tif"
    depth.write_bytes(b"fake")

    patches = [EventPatchImpactInput(lead_day=1, member_id=1, rp=2, depth_raster=depth)]

    with pytest.raises(ModuleNotFoundError):
        compute_impacts_from_event_patches(patches=patches, worldpop_tif=worldpop)
