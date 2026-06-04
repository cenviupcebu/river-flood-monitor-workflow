"""Save stage for daily flood monitoring ETL.

This module serializes basin decisions and optional map products for
downstream systems and reporting workflows.
"""

from __future__ import annotations

import csv
import json
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Tuple

from flood_ops.logging import get_logger
from .run_spec import PipelineRunSpec
from .utils import BasinRunOutput, expand_template

logger = get_logger(__name__)


_CSV_FIELDS = [
    "issue_date",
    "basin_name",
    "level",
    "name",
    "pcode",
    "tier",
    "rp",
    "p_threshold",
    "fired",
    "fire_lead",
    "probability_at_fire",
    "impact_population_threshold",
    "impact_population_at_fire",
]


def save(
    run_spec: PipelineRunSpec,
    issue_date: date,
    basin_forecasts: List[Dict[str, Any]],
) -> Tuple[List[BasinRunOutput], Path]:
    """Format basin outputs and persist final trigger files."""
    basin_results: List[BasinRunOutput] = []
    for basin in basin_forecasts:
        metadata = {
            "rule_tiers": [
                {
                    "name": r.name,
                    "rp": r.rp,
                    "p_thr": r.p_thr,
                    "n_req": r.n_req,
                    "label": r.label,
                }
                for r in run_spec.decision.rules
            ],
            "persist_days": run_spec.decision.persist_days,
            "min_lead": run_spec.decision.min_lead,
            "oep_min": run_spec.decision.oep_min,
            "oep_source": str(basin["oep_path"]),
            "impacts_source": basin["impacts_source"],
        }

        basin_results.append(
            BasinRunOutput(
                basin_name=str(basin["basin_name"]),
                issue_date=issue_date.isoformat(),
                forecast_paths=basin["forecast_paths"],
                units=basin["units"],
                metadata=metadata,
            )
        )

    output_file = _write_outputs(run_spec, issue_date, basin_results)
    return basin_results, output_file


def _serialise_basin(result: BasinRunOutput) -> Dict[str, Any]:
    return {
        "basin_name": result.basin_name,
        "issue_date": result.issue_date,
        "forecast_paths": result.forecast_paths,
        "metadata": result.metadata,
        "units": [
            {
                "unit_id": unit.unit_id,
                "level": unit.level,
                "name": unit.name,
                "pcode": unit.pcode,
                "tiers": [
                    {
                        "tier": tier.tier,
                        "rp": tier.rp,
                        "p_threshold": tier.p_threshold,
                        "fired": tier.fired,
                        "fire_lead": tier.fire_lead,
                        "probability_at_fire": tier.probability_at_fire,
                        "impact_population_threshold": tier.impact_population_threshold,
                        "impact_population_at_fire": tier.impact_population_at_fire,
                    }
                    for tier in unit.tiers
                ],
            }
            for unit in result.units
        ],
    }


def _plot_maps(
    run_spec: PipelineRunSpec,
    csv_path: Path,
    output_dir: Path,
) -> List[Path]:
    """Create per-basin and per-lead maps for activated admin areas."""
    try:
        import geopandas as gpd
        import matplotlib.pyplot as plt
        import pandas as pd
    except ImportError as exc:
        logger.warning("Skipping map plotting due to missing dependency: %s", exc)
        return []

    adm3_geojson_path = Path(run_spec.detection.adm3_geojson)
    if not adm3_geojson_path.exists():
        logger.warning("Skipping map plotting: ADM3 GeoJSON not found: %s", adm3_geojson_path)
        return []

    if not csv_path.exists():
        logger.warning("Skipping map plotting: CSV output not found: %s", csv_path)
        return []

    df = pd.read_csv(csv_path)
    if df.empty:
        logger.info("Skipping map plotting: CSV has no rows")
        return []

    # Keep only activated rows that have a valid lead day.
    fired_df = df.loc[df["fired"].astype(str).str.lower() == "true"].copy()
    fired_df["fire_lead"] = pd.to_numeric(fired_df["fire_lead"], errors="coerce")
    fired_df = fired_df.loc[fired_df["fire_lead"].notna()].copy()
    if fired_df.empty:
        logger.info("Skipping map plotting: no activated rows in CSV")
        return []

    fired_df["fire_lead"] = fired_df["fire_lead"].astype(int)
    fired_df = fired_df.loc[fired_df["level"].astype(str).str.upper() == "ADM3"].copy()
    if fired_df.empty:
        logger.info("Skipping map plotting: no ADM3 activated rows in CSV")
        return []

    fired_df["pcode"] = fired_df["pcode"].astype(str).str.strip()

    tier_rank = {"T1": 1, "T2": 2, "T3": 3}
    tier_colors = {"T1": "#FFD54F", "T2": "#FB8C00", "T3": "#D32F2F"}
    fired_df["tier_rank"] = fired_df["tier"].map(tier_rank).fillna(0).astype(int)

    # Select one tier per unit (highest activated tier) for each basin and lead.
    fired_df = (
        fired_df.sort_values("tier_rank")
        .groupby(["basin_name", "fire_lead", "pcode"], as_index=False)
        .last()
    )

    adm3_gdf = gpd.read_file(adm3_geojson_path)
    expected_unit_col = "adm3_pcode"
    pcode_col = None
    for col in adm3_gdf.columns:
        if expected_unit_col.lower() in col.lower():
            pcode_col = col
            break
    if pcode_col is None:
        raise ValueError(
            "Cannot detect admin unit column in ADM3 GeoJSON "
            f"(expected column like {expected_unit_col})"
        )

    admin_gdf = adm3_gdf[[pcode_col, "geometry"]].copy()
    admin_gdf = admin_gdf.rename(columns={pcode_col: "adm3_pcode"})
    admin_gdf["adm3_pcode"] = admin_gdf["adm3_pcode"].astype(str).str.strip()

    map_dir = output_dir / "maps"
    map_dir.mkdir(parents=True, exist_ok=True)
    map_paths: List[Path] = []

    # Plot one map for each basin and activated lead day.
    for (basin_name, fire_lead), lead_rows in fired_df.groupby(["basin_name", "fire_lead"]):
        basin_rows = df.loc[
            (df["basin_name"] == basin_name)
            & (df["level"].astype(str).str.upper() == "ADM3")
        ].copy()
        basin_rows["pcode"] = basin_rows["pcode"].astype(str).str.strip()

        basin_units = basin_rows["pcode"].dropna().unique().tolist()
        basin_gdf = admin_gdf.loc[admin_gdf["adm3_pcode"].isin(basin_units)].copy()
        if basin_gdf.empty:
            logger.warning(
                "Skipping map for basin=%s lead=%s: no admin geometry matched",
                basin_name,
                fire_lead,
            )
            continue

        merged = basin_gdf.merge(
            lead_rows[["pcode", "tier"]],
            left_on="adm3_pcode",
            right_on="pcode",
            how="left",
        )
        merged["color"] = merged["tier"].map(tier_colors)

        fig, ax = plt.subplots(figsize=(9, 9))

        # Non-activated units are boundaries only.
        merged.boundary.plot(ax=ax, color="#4D4D4D", linewidth=0.5)

        for tier, color in tier_colors.items():
            tier_gdf = merged.loc[merged["tier"] == tier]
            if not tier_gdf.empty:
                tier_gdf.plot(ax=ax, color=color, edgecolor="#333333", linewidth=0.5)

        ax.set_axis_off()
        ax.set_title(f"{basin_name} | activated alerts at lead day {fire_lead}")

        legend_handles = [
            plt.Line2D(
                [0],
                [0],
                marker="s",
                color="w",
                markerfacecolor=tier_colors["T1"],
                markersize=10,
                label="T1",
            ),
            plt.Line2D(
                [0],
                [0],
                marker="s",
                color="w",
                markerfacecolor=tier_colors["T2"],
                markersize=10,
                label="T2",
            ),
            plt.Line2D(
                [0],
                [0],
                marker="s",
                color="w",
                markerfacecolor=tier_colors["T3"],
                markersize=10,
                label="T3",
            ),
            plt.Line2D(
                [0],
                [0],
                color="#4D4D4D",
                linewidth=1,
                label="Not activated",
            ),
        ]
        ax.legend(handles=legend_handles, loc="lower left", frameon=True)

        map_file = map_dir / f"{basin_name}_lead{fire_lead}_activated_map.png"
        fig.tight_layout()
        fig.savefig(map_file, dpi=150, bbox_inches="tight")
        plt.close(fig)

        map_paths.append(map_file)

    logger.info("Map plotting complete: %d map(s) written to %s", len(map_paths), map_dir)
    return map_paths


def _write_outputs(
    run_spec: PipelineRunSpec,
    issue_date: date,
    basin_results: List[BasinRunOutput],
) -> Path:
    """Write trigger decisions as JSON (default) or flat CSV."""
    if run_spec.output is None:
        raise ValueError("Run spec must define output.output_dir_template")

    output_dir = Path(expand_template(run_spec.output.output_dir_template, issue_date))
    output_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    output_format = run_spec.output.format.lower()

    logger.info(
        "Writing output: %d basins, format=%s, dir=%s",
        len(basin_results),
        output_format,
        output_dir,
    )

    if output_format == "csv":
        out_file = output_dir / f"trigger_decisions_{issue_date.isoformat()}_{timestamp}.csv"
        with open(out_file, "w", newline="", encoding="utf-8") as f:  # TODO: specify separator
            writer = csv.DictWriter(f, fieldnames=_CSV_FIELDS)
            writer.writeheader()
            for basin in basin_results:
                for unit in basin.units:
                    for tier in unit.tiers:
                        writer.writerow(
                            {
                                "issue_date": basin.issue_date,
                                "basin_name": basin.basin_name,
                                "level": unit.level,
                                "name": unit.name,
                                "pcode": unit.pcode,
                                "tier": tier.tier,
                                "rp": tier.rp,
                                "p_threshold": tier.p_threshold,
                                "fired": tier.fired,
                                "fire_lead": tier.fire_lead,
                                "probability_at_fire": tier.probability_at_fire,
                                "impact_population_threshold": tier.impact_population_threshold,
                                "impact_population_at_fire": tier.impact_population_at_fire,
                            }
                        )
        logger.info("CSV output written: %s", out_file)
        _plot_maps(run_spec=run_spec, csv_path=out_file, output_dir=output_dir)
        return out_file

    out_file = output_dir / f"trigger_decisions_{issue_date.isoformat()}_{timestamp}.json"
    payload = {
        "run_name": run_spec.run_name,
        "generated_at_utc": datetime.utcnow().isoformat() + "Z",
        "issue_date": issue_date.isoformat(),
        "basins": [_serialise_basin(b) for b in basin_results],
    }
    out_file.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    logger.info("JSON output written: %s", out_file)
    return out_file
