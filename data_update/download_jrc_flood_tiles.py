#!/usr/bin/env python3
"""Download JRC flood hazard tiles for a given area of interest.

Fetches depth (or depth-reclassified) GeoTIFF tiles from the JRC CEMS-GloFAS
flood hazard FTP mirror for a set of return periods.  Tiles are selected by
intersecting the JRC tile index with the provided AOI bounding box.

Usage::

    # Download tiles for a lat/lon bounding box
    uv run python data_update/download_jrc_flood_tiles.py \\
        --bbox 117.0 14.5 123.5 19.5 \\
        --output-dir data/bronze/jrc_flood_maps

    # Restrict to specific return periods
    uv run python data_update/download_jrc_flood_tiles.py \\
        --bbox 117.0 14.5 123.5 19.5 \\
        --return-periods 10 100 500 \\
        --output-dir data/bronze/jrc_flood_maps

    # Use reclassified depth maps
    uv run python data_update/download_jrc_flood_tiles.py \\
        --bbox 117.0 14.5 123.5 19.5 \\
        --reclassified \\
        --output-dir data/bronze/jrc_flood_maps
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import geopandas as gpd
import requests
from shapely.geometry import box

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

JRC_BASE_URL = (
    "https://jeodpp.jrc.ec.europa.eu/ftp/jrc-opendata/CEMS-GLOFAS/flood_hazard/"
)
# JRC only provides these 7 return periods — no RP1 folder exists on the server.
DEFAULT_RETURN_PERIODS = [10, 20, 50, 75, 100, 200, 500]

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("download_jrc_flood_tiles")


# ---------------------------------------------------------------------------
# Core helpers
# ---------------------------------------------------------------------------


def fetch_tile_index(output_dir: Path) -> gpd.GeoDataFrame:
    """Download (and cache) the JRC tile index GeoJSON."""
    tile_index_path = output_dir / "tile_extents.geojson"
    output_dir.mkdir(parents=True, exist_ok=True)

    if not tile_index_path.exists():
        url = JRC_BASE_URL + "tile_extents.geojson"
        log.info("Downloading JRC tile index from %s", url)
        response = requests.get(url, timeout=120)
        response.raise_for_status()
        tile_index_path.write_bytes(response.content)
        log.info("Tile index saved → %s", tile_index_path)
    else:
        log.info("Using cached tile index: %s", tile_index_path)

    tiles_gdf = gpd.read_file(tile_index_path).to_crs("EPSG:4326")
    log.info("Tile index loaded: %d tiles total", len(tiles_gdf))
    return tiles_gdf


def select_tiles(tiles_gdf: gpd.GeoDataFrame, aoi: box) -> gpd.GeoDataFrame:
    """Return tiles that intersect the AOI geometry."""
    required_cols = {"id", "name"}
    if not required_cols.issubset(set(tiles_gdf.columns)):
        raise ValueError(
            f"tile_extents.geojson is missing expected columns. "
            f"Found: {list(tiles_gdf.columns)}"
        )

    selected = tiles_gdf[tiles_gdf.intersects(aoi)].copy()
    selected["tile_code"] = (
        "ID" + selected["id"].astype(str) + "_" + selected["name"].astype(str)
    )
    log.info("Tiles intersecting AOI: %d", len(selected))
    return selected


def download_tile(
    rp: int,
    tile_code: str,
    output_dir: Path,
    *,
    reclassified: bool = False,
) -> Path | None:
    """Download a single JRC flood tile; returns the local path or None on failure."""
    suffix = "_depth_reclass.tif" if reclassified else "_depth.tif"
    fname = f"{tile_code}_RP{rp}{suffix}"
    url = f"{JRC_BASE_URL}RP{rp}/{fname}"
    out_path = output_dir / fname

    if out_path.exists() and out_path.stat().st_size > 0:
        return out_path  # already cached

    try:
        response = requests.get(url, stream=True, timeout=300)
        response.raise_for_status()
        with open(out_path, "wb") as f:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    f.write(chunk)
        return out_path
    except requests.HTTPError as exc:
        log.warning("HTTP error for %s: %s", url, exc)
    except requests.RequestException as exc:
        log.warning("Request failed for %s: %s", url, exc)
    return None


def download_all_tiles(
    tile_codes: list[str],
    return_periods: list[int],
    cache_root: Path,
    *,
    reclassified: bool = False,
) -> list[dict]:
    """Download tiles for every (return_period, tile) combination.

    Returns a manifest list of dicts with keys: rp, tile_code, path.
    """
    manifest: list[dict] = []

    for rp in return_periods:
        rp_dir = cache_root / f"RP{rp}"
        rp_dir.mkdir(parents=True, exist_ok=True)

        success = 0
        for tile_code in tile_codes:
            out_path = download_tile(rp, tile_code, rp_dir, reclassified=reclassified)
            if out_path:
                manifest.append(
                    {"rp": int(rp), "tile_code": tile_code, "path": str(out_path)}
                )
                success += 1

        log.info("RP=%3dyr: %d/%d tiles downloaded", rp, success, len(tile_codes))

    return manifest


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Download JRC flood hazard depth tiles for a bounding box.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--bbox",
        nargs=4,
        type=float,
        metavar=("LON_MIN", "LAT_MIN", "LON_MAX", "LAT_MAX"),
        required=True,
        help="Bounding box in WGS-84 degrees (lon_min lat_min lon_max lat_max).",
    )
    parser.add_argument(
        "--return-periods",
        nargs="+",
        type=int,
        default=DEFAULT_RETURN_PERIODS,
        metavar="RP",
        help=(
            f"Return periods to download (years). "
            f"Defaults to all available: {DEFAULT_RETURN_PERIODS}"
        ),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("data/bronze/jrc_flood_maps"),
        help="Root directory for cached tiles (default: data/bronze/jrc_flood_maps).",
    )
    parser.add_argument(
        "--reclassified",
        action="store_true",
        default=False,
        help="Download depth_reclass.tif files instead of depth.tif.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    # Validate return periods against what JRC actually provides
    invalid_rps = [rp for rp in args.return_periods if rp not in DEFAULT_RETURN_PERIODS]
    if invalid_rps:
        log.error(
            "Requested return periods not available from JRC: %s. "
            "Valid options: %s",
            invalid_rps,
            DEFAULT_RETURN_PERIODS,
        )
        return 1

    lon_min, lat_min, lon_max, lat_max = args.bbox
    aoi = box(lon_min, lat_min, lon_max, lat_max)
    log.info(
        "AOI bbox: lon=[%.4f, %.4f]  lat=[%.4f, %.4f]",
        lon_min,
        lon_max,
        lat_min,
        lat_max,
    )
    log.info("Return periods requested: %s", args.return_periods)
    log.info("Output directory: %s", args.output_dir)
    log.info("Reclassified depth maps: %s", args.reclassified)

    # 1. Tile index
    tiles_gdf = fetch_tile_index(args.output_dir)

    # 2. Select tiles intersecting the AOI
    tiles_sel = select_tiles(tiles_gdf, aoi)
    if tiles_sel.empty:
        log.error("No tiles found for the given bounding box.")
        return 1

    tile_codes = sorted(tiles_sel["tile_code"].dropna().unique().tolist())
    log.info("Tile codes selected: %s", tile_codes)

    # 3. Download
    manifest = download_all_tiles(
        tile_codes,
        args.return_periods,
        args.output_dir,
        reclassified=args.reclassified,
    )

    if not manifest:
        log.error("No tiles were downloaded successfully.")
        return 1

    log.info(
        "Download complete: %d tiles saved to %s",
        len(manifest),
        args.output_dir,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
