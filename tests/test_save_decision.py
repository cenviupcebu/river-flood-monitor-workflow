from __future__ import annotations

from datetime import date
from pathlib import Path

from river_flood_monitoring.etl.run_spec import (
    DecisionSettings,
    OutputSettings,
    PipelineRunSpec,
)
from river_flood_monitoring.etl.save import save
from river_flood_monitoring.etl.utils import TierDecision, UnitDecision


def _build_run_spec(tmp_path: Path) -> PipelineRunSpec:
    return PipelineRunSpec(
        run_name="daily_flood_monitoring",
        decision=DecisionSettings(),
        output=OutputSettings(
            output_dir_template=str(tmp_path / "{date}"),
        ),
    )


def _build_basin_forecast(fired: bool) -> dict:
    return {
        "basin_name": "cagayan",
        "forecast_paths": ["sample.nc"],
        "units": [
            UnitDecision(
                unit_id="ADM3-001",
                level="ADM3",
                name="Sample Unit",
                pcode="PH0201501",  # Valid Cagayan ADM3 (starts with PH02015)
                tiers=[
                    TierDecision(
                        tier="T1",
                        rp=2,
                        p_threshold=0.5,
                        fired=fired,
                        fire_lead=5 if fired else None,
                        probability_at_fire=0.62 if fired else None,
                        impact_population_threshold=100.0,
                        impact_population_at_fire=120.0 if fired else None,
                    )
                ],
            ),
            UnitDecision(
                unit_id="ADM2-001",
                level="ADM2",
                name="Cagayan Province",
                pcode="PH02015",  # Cagayan ADM2
                tiers=[
                    TierDecision(
                        tier="T1",
                        rp=2,
                        p_threshold=0.5,
                        fired=fired,
                        fire_lead=5 if fired else None,
                        probability_at_fire=0.62 if fired else None,
                        impact_population_threshold=100.0,
                        impact_population_at_fire=120.0 if fired else None,
                    )
                ],
            ),
        ],
        "oep_path": Path("config/risk_profiles/cagayan_oep_curves_all_units.json"),
        "impacts_source": "evt",
    }


def test_save_writes_decision_true_when_any_tier_fired(tmp_path: Path) -> None:
    run_spec = _build_run_spec(tmp_path)
    issue_date = date(2026, 6, 1)

    save_outputs = save(run_spec, issue_date, [_build_basin_forecast(fired=True)])

    # Check operational_information file (ADM3)
    operational_information_file = save_outputs["operational_information_file"]
    assert operational_information_file is not None
    assert operational_information_file.exists()
    assert operational_information_file.name.startswith("operational_information_2026-06-01_")

    # Check activation file (ADM2)
    activation_file = save_outputs["activation_file"]
    assert activation_file is not None
    assert activation_file.exists()
    assert activation_file.name.startswith("activation_2026-06-01_")

    decision_path = operational_information_file.parent / "decision.txt"
    assert save_outputs["decision_summary_file"] == decision_path
    assert decision_path.exists()
    assert decision_path.read_text(encoding="utf-8") == "triggered=True\n"


def test_save_writes_decision_false_when_no_tier_fired(tmp_path: Path) -> None:
    run_spec = _build_run_spec(tmp_path)
    issue_date = date(2026, 6, 1)

    save_outputs = save(run_spec, issue_date, [_build_basin_forecast(fired=False)])

    # Check operational_information file (ADM3)
    operational_information_file = save_outputs["operational_information_file"]
    assert operational_information_file is not None
    assert operational_information_file.exists()

    # Check activation file (ADM2)
    activation_file = save_outputs["activation_file"]
    assert activation_file is not None
    assert activation_file.exists()

    decision_path = operational_information_file.parent / "decision.txt"
    assert save_outputs["decision_summary_file"] == decision_path
    assert decision_path.exists()
    assert decision_path.read_text(encoding="utf-8") == "triggered=False\n"


def test_save_splits_into_activation_and_operational_information_files(tmp_path: Path) -> None:
    run_spec = _build_run_spec(tmp_path)
    issue_date = date(2026, 6, 1)

    save_outputs = save(run_spec, issue_date, [_build_basin_forecast(fired=True)])

    # Verify activation file (ADM2) exists
    activation_file = save_outputs["activation_file"]
    assert activation_file is not None
    assert activation_file.exists()
    assert activation_file.suffix == ".csv"
    assert activation_file.name.startswith("activation_2026-06-01_")

    # Verify operational_information file (ADM3) exists
    operational_information_file = save_outputs["operational_information_file"]
    assert operational_information_file is not None
    assert operational_information_file.exists()
    assert operational_information_file.suffix == ".csv"
    assert operational_information_file.name.startswith("operational_information_2026-06-01_")

    # Verify no trigger_decisions file is created
    output_dir = activation_file.parent
    trigger_decisions_files = list(output_dir.glob("trigger_decisions_*.csv"))
    assert len(trigger_decisions_files) == 0
