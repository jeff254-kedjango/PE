"""
Fetch building footprints + real heights from Google Open Buildings via Earth Engine.

This is the production footprint source for AOIs whose registry entry declares
`footprint_source == "open_buildings"` (currently Huruma). It replaces the OSM
path (`osm_footprints.py`) for those AOIs, giving us:

  - far higher coverage in informal settlements (Huruma: ~13k OB footprints vs
    ~7.4k from OSM), and
  - a REAL per-building height for every footprint (OSM had heights for ~16%).

Two GEE assets are joined:
  - GOOGLE/Research/open-buildings/v3/polygons   — footprint geometry + confidence
  - GOOGLE/Research/open-buildings-temporal/v1   — `building_height` band (m, AGL),
    sampled per footprint (mean over the polygon) from the latest temporal image.

Output GeoParquet (a superset of the OSM footprint contract, so the downstream
reader `phenomena.py:_real_buildings` is source-agnostic — it reads whatever
columns are present):

    osm_id            : int64    (always null for this source; kept for symmetry)
    open_buildings_id : string   (native OB id = full_plus_code)
    geom_wkb          : binary
    centroid_lon      : float64
    centroid_lat      : float64
    n_floors          : int16    (derived: round(height_m / 3.0); for physics only)
    height_m          : float32  (REAL Open Buildings height, AGL)
    confidence        : float32  (OB footprint confidence, 0..1)
    built_year        : int16    (always null; OB carries no construction date)

Run from backend/:
    python -m scripts.open_buildings_footprints --aoi huruma

GEE project id is read from $EE_PROJECT (falls back to the project used elsewhere
in this repo, see geospatial_data/fetch_assets.py). This is a build-time tool;
the served app reads only the parquet/duckdb, never GEE.
"""

from __future__ import annotations

import argparse
import math
import os
import sys
from pathlib import Path

from scripts.aois import AOI, REGISTRY, by_code, bbox

BACKEND_DIR = Path(__file__).resolve().parents[1]
FOOTPRINT_DIR = BACKEND_DIR / "data" / "footprints"

OB_POLYGONS = "GOOGLE/Research/open-buildings/v3/polygons"
OB_TEMPORAL = "GOOGLE/Research/open-buildings-temporal/v1"
HEIGHT_SCALE_M = 4  # building_height native resolution

# Default GEE project — matches geospatial_data/fetch_assets.py. Override with
# $EE_PROJECT so this isn't silently pinned to one account.
DEFAULT_EE_PROJECT = "ee-kwemangenyagrowa"

# A single getInfo() over ~13k features can exceed Earth Engine's response
# limits. We page the AOI bbox into an N×N grid of sub-tiles and fetch each
# tile's reduced FeatureCollection separately, then concatenate. Tiles are
# disjoint half-open cells, so no footprint is fetched twice.
TILE_GRID = 4


def _init_ee() -> None:
    import ee

    project = os.environ.get("EE_PROJECT", DEFAULT_EE_PROJECT)
    try:
        ee.Initialize(project=project)
    except Exception as e:  # noqa: BLE001 — surface the auth failure plainly
        print(
            f"  ✗ Earth Engine init failed (project={project!r}): {e}\n"
            f"    Authenticate with `earthengine authenticate` or set $EE_PROJECT.",
            file=sys.stderr,
        )
        sys.exit(1)


def _tile_bboxes(aoi: AOI, n: int) -> list[tuple[float, float, float, float]]:
    """Split the AOI bbox into an n×n grid of disjoint sub-bboxes."""
    minlon, minlat, maxlon, maxlat = bbox(aoi)
    dlon = (maxlon - minlon) / n
    dlat = (maxlat - minlat) / n
    tiles = []
    for ix in range(n):
        for iy in range(n):
            tiles.append((
                minlon + ix * dlon,
                minlat + iy * dlat,
                minlon + (ix + 1) * dlon,
                minlat + (iy + 1) * dlat,
            ))
    return tiles


def _fetch_tile(ee, rect, height_img) -> list[dict]:
    """Fetch one tile: OB footprints clipped to `rect`, each annotated with the
    mean `building_height` over its polygon. Returns raw GEE feature dicts."""
    geom = ee.Geometry.Rectangle(list(rect))
    fc = ee.FeatureCollection(OB_POLYGONS).filterBounds(geom)
    sampled = height_img.reduceRegions(
        collection=fc,
        reducer=ee.Reducer.mean(),
        scale=HEIGHT_SCALE_M,
    )
    return sampled.getInfo().get("features", [])


def fetch_aoi(aoi: AOI) -> Path:
    """Download Open Buildings footprints + heights for an AOI; write GeoParquet."""
    import ee
    import pyarrow as pa
    import pyarrow.parquet as pq
    from shapely.geometry import shape
    from shapely.wkb import dumps as wkb_dumps

    FOOTPRINT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = FOOTPRINT_DIR / f"{aoi.code}.parquet"

    full = ee.Geometry.Rectangle(list(bbox(aoi)))
    height_img = (
        ee.ImageCollection(OB_TEMPORAL)
        .filterBounds(full)
        .sort("system:time_start", False)
        .first()
        .select("building_height")
    )

    ob_ids: list[str | None] = []
    geoms_wkb: list[bytes] = []
    centroids: list[tuple[float, float]] = []
    n_floors: list[int | None] = []
    heights: list[float | None] = []
    confidences: list[float | None] = []

    seen: set[str] = set()  # dedupe across tile boundaries by plus-code
    tiles = _tile_bboxes(aoi, TILE_GRID)
    for t_i, rect in enumerate(tiles):
        feats = _fetch_tile(ee, rect, height_img)
        print(f"  tile {t_i + 1}/{len(tiles)}: {len(feats)} features")
        for f in feats:
            props = f.get("properties", {}) or {}
            plus = props.get("full_plus_code")
            if plus and plus in seen:
                continue
            try:
                poly = shape(f["geometry"])
                if not poly.is_valid:
                    poly = poly.buffer(0)
                if poly.is_empty:
                    continue
            except Exception:  # noqa: BLE001
                continue

            h_raw = props.get("mean")  # mean building_height over the footprint
            height_m = float(h_raw) if h_raw is not None and math.isfinite(h_raw) else None
            # Derive a floor count only so the synthetic velocity/coherence physics
            # (which key on n_floors) keep working; height_m above is the truth.
            nf = max(1, round(height_m / 3.0)) if height_m and height_m > 0 else None

            if plus:
                seen.add(plus)
            ob_ids.append(plus)
            geoms_wkb.append(wkb_dumps(poly))
            c = poly.centroid
            centroids.append((c.x, c.y))
            n_floors.append(nf)
            heights.append(height_m)
            conf = props.get("confidence")
            confidences.append(float(conf) if conf is not None else None)

    table = pa.table({
        "osm_id":            pa.array([None] * len(ob_ids),            type=pa.int64()),
        "open_buildings_id": pa.array(ob_ids,                          type=pa.string()),
        "geom_wkb":          pa.array(geoms_wkb,                       type=pa.binary()),
        "centroid_lon":      pa.array([c[0] for c in centroids],       type=pa.float64()),
        "centroid_lat":      pa.array([c[1] for c in centroids],       type=pa.float64()),
        "n_floors":          pa.array(n_floors,                        type=pa.int16()),
        "height_m":          pa.array(heights,                         type=pa.float32()),
        "confidence":        pa.array(confidences,                     type=pa.float32()),
        "built_year":        pa.array([None] * len(ob_ids),            type=pa.int16()),
    })
    pq.write_table(table, out_path, compression="zstd")
    print(f"  ✓ wrote {table.num_rows} footprints → {out_path}")
    return out_path


def main() -> None:
    p = argparse.ArgumentParser(
        description="Download Google Open Buildings footprints + heights per AOI (GEE)",
    )
    p.add_argument("--aoi", action="append", help="AOI code (repeatable); default = all open_buildings AOIs")
    args = p.parse_args()

    requested = [by_code(c) for c in args.aoi] if args.aoi else REGISTRY
    aois = [a for a in requested if a.footprint_source == "open_buildings"]
    skipped = [a.code for a in requested if a.footprint_source != "open_buildings"]
    if skipped:
        print(f"Skipping non-open_buildings AOIs: {', '.join(skipped)} (use scripts.osm_footprints)")
    if not aois:
        print("No open_buildings AOIs to fetch.")
        return

    _init_ee()
    for aoi in aois:
        print(f"\n=== {aoi.code} ({aoi.name}) ===")
        fetch_aoi(aoi)


if __name__ == "__main__":
    main()
