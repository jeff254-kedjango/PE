"""
Fetch building footprints for an AOI from OpenStreetMap (Overpass API).

For both Huruma and Mombasa, OSM is the simplest path to real footprints. The
`aois.py` registry declares `footprint_source` per AOI — for huruma it's
"open_buildings" (Google), but Open Buildings download requires either GEE or
a 36-GB CSV download, neither of which we want as a build-time dependency.
OSM coverage in Huruma is sparse but non-zero, and Mombasa is well-covered.
We downgrade Huruma to OSM here and accept the lower density.

Output: GeoParquet with columns:
    osm_id            : int64
    geom_wkb          : binary
    centroid_lon      : float64
    centroid_lat      : float64
    n_floors          : int16   (parsed from OSM `building:levels` tag; null otherwise)
    height_m          : float32 (n_floors × 3.0, or OSM `height` tag if present)
    built_year        : int16   (from `start_date`, null otherwise)

Run from backend/:
    python -m scripts.osm_footprints --aoi huruma
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import requests

from scripts.aois import AOI, REGISTRY, by_code, bbox

BACKEND_DIR = Path(__file__).resolve().parents[1]
FOOTPRINT_DIR = BACKEND_DIR / "data" / "footprints"
OVERPASS = "https://overpass-api.de/api/interpreter"
TIMEOUT_S = 180

# Overpass blocks generic UAs to keep bot traffic down. Identify ourselves with
# a contact-able string (project name; replace email before going to production).
HTTP_HEADERS = {"User-Agent": "infra-proptech/0.1 (build-time footprint ingest)"}


def _overpass_query(aoi: AOI) -> str:
    """Overpass QL: every way/relation tagged building=* within the AOI bbox.

    We ask for geometry inline (`out geom;`) so we get coordinates back without
    needing a second round-trip per node. Cost scales with bbox area only.
    """
    minlon, minlat, maxlon, maxlat = bbox(aoi)
    return f"""
[out:json][timeout:{TIMEOUT_S}];
(
  way["building"]({minlat},{minlon},{maxlat},{maxlon});
  relation["building"]({minlat},{minlon},{maxlat},{maxlon});
);
out geom;
""".strip()


def _parse_height(tags: dict) -> tuple[int | None, float | None]:
    """Extract (n_floors, height_m) from OSM tags. Either may be null.

    Precedence:
      - `height` tag wins for height_m (parse "12 m", "12m", "12")
      - `building:levels` tag wins for n_floors
      - Falls back to height_m = n_floors * 3.0 if only floors are known
    """
    h_raw = tags.get("height")
    height_m: float | None = None
    if h_raw:
        m = re.match(r"^\s*(\d+(?:\.\d+)?)", h_raw)
        if m:
            height_m = float(m.group(1))

    floors_raw = tags.get("building:levels")
    n_floors: int | None = None
    if floors_raw:
        m = re.match(r"^\s*(\d+)", floors_raw)
        if m:
            n_floors = int(m.group(1))

    if height_m is None and n_floors is not None:
        height_m = n_floors * 3.0
    if n_floors is None and height_m is not None:
        n_floors = max(1, round(height_m / 3.0))

    return n_floors, height_m


def _parse_year(tags: dict) -> int | None:
    raw = tags.get("start_date") or tags.get("year_of_construction")
    if not raw:
        return None
    m = re.match(r"^\s*(\d{4})", raw)
    return int(m.group(1)) if m else None


def fetch_aoi(aoi: AOI) -> Path:
    """Download all building footprints for an AOI, write a GeoParquet to disk."""
    import pyarrow as pa
    import pyarrow.parquet as pq
    from shapely.geometry import Polygon
    from shapely.wkb import dumps as wkb_dumps

    FOOTPRINT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = FOOTPRINT_DIR / f"{aoi.code}.parquet"

    query = _overpass_query(aoi)
    print(f"  POST {OVERPASS} ({len(query)} bytes)")
    r = requests.post(OVERPASS, data={"data": query}, headers=HTTP_HEADERS, timeout=TIMEOUT_S + 30)
    r.raise_for_status()
    data = r.json()
    elements = data.get("elements", [])
    print(f"  {len(elements)} elements returned")

    osm_ids: list[int] = []
    geoms_wkb: list[bytes] = []
    centroids: list[tuple[float, float]] = []
    n_floors: list[int | None] = []
    heights: list[float | None] = []
    years: list[int | None] = []

    for el in elements:
        if el["type"] != "way":
            # Relations require ring assembly we don't want to write twice;
            # skip for now — relation buildings are <2% of urban OSM.
            continue
        coords = [(g["lon"], g["lat"]) for g in el.get("geometry", [])]
        if len(coords) < 4:
            continue
        # Close the ring if OSM gave us an open one.
        if coords[0] != coords[-1]:
            coords.append(coords[0])
        try:
            poly = Polygon(coords)
            if not poly.is_valid:
                poly = poly.buffer(0)
            if poly.is_empty:
                continue
        except Exception:
            continue

        tags = el.get("tags", {}) or {}
        nf, hm = _parse_height(tags)

        osm_ids.append(int(el["id"]))
        geoms_wkb.append(wkb_dumps(poly))
        c = poly.centroid
        centroids.append((c.x, c.y))
        n_floors.append(nf)
        heights.append(hm)
        years.append(_parse_year(tags))

    table = pa.table({
        "osm_id":       pa.array(osm_ids,                 type=pa.int64()),
        "geom_wkb":     pa.array(geoms_wkb,               type=pa.binary()),
        "centroid_lon": pa.array([c[0] for c in centroids], type=pa.float64()),
        "centroid_lat": pa.array([c[1] for c in centroids], type=pa.float64()),
        "n_floors":     pa.array(n_floors,                type=pa.int16()),
        "height_m":     pa.array(heights,                 type=pa.float32()),
        "built_year":   pa.array(years,                   type=pa.int16()),
    })
    pq.write_table(table, out_path, compression="zstd")
    print(f"  ✓ wrote {table.num_rows} footprints → {out_path}")
    return out_path


def main() -> None:
    p = argparse.ArgumentParser(description="Download OSM building footprints per AOI")
    p.add_argument("--aoi", action="append", help="AOI code (repeatable); default = all")
    args = p.parse_args()

    aois = [by_code(c) for c in args.aoi] if args.aoi else REGISTRY
    for aoi in aois:
        print(f"\n=== {aoi.code} ({aoi.name}) ===")
        fetch_aoi(aoi)


if __name__ == "__main__":
    main()
