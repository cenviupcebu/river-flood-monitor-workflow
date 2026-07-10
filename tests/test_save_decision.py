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
                pcode="PH0123",
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
            )
        ],
        "oep_path": Path("config/risk_profiles/cagayan_oep_curves_all_units.json"),
        "impacts_source": "evt",
    }


def test_save_writes_decision_true_when_any_tier_fired(tmp_path: Path) -> None:
    run_spec = _build_run_spec(tmp_path)
    issue_date = date(2026, 6, 1)

    save_outputs = save(run_spec, issue_date, [_build_basin_forecast(fired=True)])
    output_file = save_outputs["main_output_file"]

    decision_path = output_file.parent / "decision.txt"
    assert save_outputs["decision_summary_file"] == decision_path
    assert decision_path.exists()
    assert decision_path.read_text(encoding="utf-8") == "triggered=True\n"


def test_save_writes_decision_false_when_no_tier_fired(tmp_path: Path) -> None:
    run_spec = _build_run_spec(tmp_path)
    issue_date = date(2026, 6, 1)

    save_outputs = save(run_spec, issue_date, [_build_basin_forecast(fired=False)])
    output_file = save_outputs["main_output_file"]

    decision_path = output_file.parent / "decision.txt"
    assert save_outputs["decision_summary_file"] == decision_path
    assert decision_path.exists()
    assert decision_path.read_text(encoding="utf-8") == "triggered=False\n"


def test_save_output_uses_trigger_decisions_filename_pattern(tmp_path: Path) -> None:
    run_spec = _build_run_spec(tmp_path)
    issue_date = date(2026, 6, 1)

    save_outputs = save(run_spec, issue_date, [_build_basin_forecast(fired=True)])

    assert save_outputs["main_output_file"].exists()
    assert save_outputs["main_output_file"].suffix == ".csv"
    assert save_outputs["main_output_file"].name.startswith("trigger_decisions_2026-06-01_")
