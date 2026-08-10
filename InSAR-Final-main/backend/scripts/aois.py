"""
AOI registry. One module-level constant per area of interest, holding everything
the seeder, pipeline, and UI need to know about it. New AOIs go here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Phenomenon = Literal["informal_settlement_subsidence", "coastal_subsidence"]
FootprintSource = Literal["open_buildings", "osm", "synthetic"]


@dataclass(frozen=True)
class AOI:
    code: str
    name: str
    center_lon: float
    center_lat: float
    side_m: float                       # AOI is a square of this side around the centroid
    phenomenon: Phenomenon
    footprint_source: FootprintSource   # which real source we'll use when we leave synthetic
    n_synthetic_buildings: int          # for the seeder
    narrative: str                      # short copy shown in the UI sidebar
    # ARCHITECTURE_THREE A3 — explicit InSAR reference point.
    # Chosen *outside* the AOI on stable, high-coherence terrain so all
    # velocities are anchored to a documented zero. Surfaced in the UI as the
    # ⚓ pin so viewers can see what their measurements are relative to.
    reference_lon: float
    reference_lat: float
    reference_note: str                 # ≤120 chars, shown as the pin tooltip
    # ARCHITECTURE_THREE A3 — processing geometry decoupled from display geometry.
    # `side_m` is the 2 km tile the UI/bundle shows; the MintPy load subset can be
    # wider so it (a) is identical across all interferograms and (b) contains the
    # reference anchor, which sits outside the display tile. None → processing box
    # == display box. Defaulted, so it must stay last among the fields.
    processing_side_m: float | None = None
    # ARCHITECTURE_THREE A3 — does this AOI resolve to an EXTERNAL stable anchor
    # (documented zero outside the tile, e.g. Karura bedrock)? True for AOIs whose
    # reference sits on stable ground reachable within tropospheric coverage. False
    # when the tile has no internal stable ground AND its external anchor is out of
    # GACOS range (South C): there, MintPy's internal reference is itself moving, so
    # absolute velocities carry an un-removable tile-wide offset. join_insar then
    # classifies on velocity RELATIVE to the AOI's coherent-stable-bulk median (the
    # honest differential signal). Defaulted True; must stay last among the fields.
    external_anchor: bool = True


HURUMA = AOI(
    code="huruma",
    name="Huruma, Nairobi",
    center_lon=36.8740,
    center_lat=-1.2510,
    side_m=2000.0,
    # Karura anchor is ~4396 m NW of centre — outside the 2 km display tile. A
    # 10 km processing box (half-side 5000 m) contains it with ~600 m margin.
    processing_side_m=10000.0,
    phenomenon="informal_settlement_subsidence",
    footprint_source="open_buildings",
    n_synthetic_buildings=1500,
    narrative=(
        "Dense informal settlement on mixed black-cotton and alluvial soils, "
        "with a tributary running diagonally through the block. Footprints are "
        "ML-derived (Google Open Buildings) because OSM coverage here is sparse. "
        "InSAR signal is noisy on corrugated-iron roofs — coherence is surfaced "
        "in the UI so users can see where to trust the velocity."
    ),
    # Karura Forest granite edge — stable Precambrian basement, high temporal
    # coherence in S1 stacks, no construction, ~4 km NW of Huruma centroid.
    reference_lon=36.8345,
    reference_lat=-1.2391,
    reference_note=(
        "Karura Forest bedrock outcrop — stable Precambrian basement, no "
        "construction. Anchors all Huruma velocities to a documented zero."
    ),
)

MOMBASA = AOI(
    code="mombasa",
    name="Mombasa Old Town / Kilindini",
    center_lon=39.6680,
    center_lat=-4.0610,
    side_m=2000.0,
    # Changamwe anchor is ~3840 m W of centre — outside the 2 km display tile. A
    # 9 km processing box (half-side 4500 m) contains it with ~660 m margin.
    processing_side_m=9000.0,
    phenomenon="coastal_subsidence",
    footprint_source="open_buildings",
    n_synthetic_buildings=1100,
    narrative=(
        "Coastal urban tile spanning Old Town and reclaimed land near Kilindini. "
        "Footprints are ML-derived (Google Open Buildings) for dense, uniform "
        "coverage. Concrete and bare surfaces yield higher InSAR coherence than "
        "Huruma. Watch the reclaimed-land cohort on engineered fill (real "
        "SoilGrids reclaim_fill ground): subsidence there is real, slow, and "
        "well-measured by Sentinel-1."
    ),
    # Changamwe Hill coral-platform exposure — far from coastline + reclaim,
    # high coherence on both ASC/DESC tracks, ~3 km W of Mombasa centroid.
    reference_lon=39.6395,
    reference_lat=-4.0265,
    reference_note=(
        "Changamwe Hill coral platform — inland, stable, far from reclaim fill. "
        "Anchors all Mombasa velocities to a documented zero."
    ),
)


KILELESHWA = AOI(
    code="kileleshwa",
    name="Kileleshwa, Nairobi",
    center_lon=36.7830,
    center_lat=-1.2830,
    side_m=2000.0,
    # 1800 m processing box (NOT the display 2 km, NOT a Karura-reaching widening).
    # Like South C, Kileleshwa sits at the WEST edge of the Nairobi GACOS grid: a
    # full 2 km box's gdalwarp-snapped footprint overruns the grid west edge by
    # ~62 m and crashes tropo_gacos. 1800 m clears it by ~98 m. Karura (−1.2391) is
    # north of even the GACOS north edge, so the shared bedrock anchor is out of
    # tropospheric coverage here; we snap a LOCAL anchor and de-mean (see below).
    processing_side_m=1800.0,
    phenomenon="informal_settlement_subsidence",
    footprint_source="open_buildings",
    n_synthetic_buildings=1200,
    narrative=(
        "Established upmarket Nairobi suburb on red volcanic and black-cotton "
        "soils, with leafy plots transitioning to dense mid-rise apartment "
        "redevelopment. Footprints are ML-derived (Google Open Buildings). "
        "Shares the ascending path-57 Sentinel-1 frame with Huruma, anchored to a "
        "local coherent-stable reference inside the tile."
    ),
    # The reference pixel MintPy actually wrote (REF_Y=12, REF_X=11 of the grid).
    # Unlike South C, Kileleshwa's coherent bulk is too spread (vel std ~3.8 mm/yr)
    # for _snap_reference_lalo's tightness guard to pass, so it kept the cold-start
    # centroid anchor rather than snapping a tight stable medoid. That's fine: with
    # external_anchor=False the join de-means to the coherent-bulk median, so the
    # threat verdict is reference-invariant and the pin only marks the SBAS zero.
    reference_lon=36.78293,
    reference_lat=-1.28309,
    reference_note=(
        "Local in-tile SBAS reference (Kileleshwa). Karura bedrock is out of GACOS "
        "coverage at this AOI's latitude, so velocities are anchored to this "
        "in-tile zero and de-meaned to the coherent-bulk median — read as "
        "differential motion, not absolute."
    ),
    # West-edge-of-GACOS tile with near-uniform motion and no reachable external
    # anchor → de-mean to the coherent-bulk median in join_insar (see South C).
    external_anchor=False,
)

KILIMANI = AOI(
    code="kilimani",
    name="Kilimani, Nairobi",
    center_lon=36.7870,
    center_lat=-1.2900,
    side_m=2000.0,
    # Processing box = display tile (None). A full 2 km box's snapped footprint
    # clears the Nairobi GACOS grid on all edges (~446 m west margin), unlike
    # Kileleshwa. Karura (−1.2391) is out of GACOS coverage at this latitude, so
    # the shared bedrock anchor is unreachable here; snap a LOCAL anchor + de-mean.
    processing_side_m=None,
    phenomenon="informal_settlement_subsidence",
    footprint_source="open_buildings",
    n_synthetic_buildings=1200,
    narrative=(
        "Dense, rapidly redeveloping Nairobi suburb where low-rise plots are "
        "being replaced by high-rise apartments on mixed volcanic and "
        "black-cotton soils — a setting where construction loading and "
        "differential settlement are plausible. Footprints are ML-derived "
        "(Google Open Buildings). Shares Huruma's ascending path-57 frame, "
        "anchored to a local coherent-stable reference inside the tile."
    ),
    # The coherent-stable pixel MintPy snapped to (REF_Y=21, REF_X=15). Unlike
    # Kileleshwa, Kilimani's coherent bulk is tight (vel std ~1.6 mm/yr), so
    # _snap_reference_lalo found a clustered near-zero-velocity medoid and pinned
    # it — a genuine in-tile stable anchor. With external_anchor=False the join
    # still de-means to the coherent-bulk median (here a near-no-op, bulk median
    # −0.03 mm/yr), so velocities read as differential and the pin marks the zero.
    reference_lon=36.78860,
    reference_lat=-1.29590,
    reference_note=(
        "Local coherent-stable reference inside the Kilimani tile (snapped by "
        "MintPy to a high-coherence, near-zero-velocity, clustered pixel). Karura "
        "bedrock is out of GACOS coverage at this AOI's latitude, so velocities are "
        "anchored to this in-tile stable zero and read as differential."
    ),
    # Near-uniform-motion tile with no reachable external anchor → de-mean to the
    # coherent-bulk median in join_insar (see South C).
    external_anchor=False,
)


SOUTH_C = AOI(
    code="south_c",
    name="South C, Nairobi",
    # Centered on the Kiganjo / Muhoho Avenue junction near Nairobi South
    # Hospital and South C Shopping Centre — the vicinity of the 2 Jan 2026
    # mixed-use building collapse. County situation report cites Plot 209/5909/10
    # (Lang'ata Sub-County, Southern Borough); the NCA project registration cites
    # 68/1306 — the discrepancy is the approved (12-floor) vs as-built record.
    # Approximate centroid: public reporting gave landmarks + plot numbers, not
    # exact GPS, so this is the junction proxy, not a surveyed plot corner.
    center_lon=36.8320,
    center_lat=-1.3140,
    side_m=2000.0,
    # No processing-box widening (processing == display tile). South C sits at
    # the SOUTHERN edge of the Nairobi GACOS grid (grid south edge -1.3237, only
    # ~1.1 km below this centroid), so the GACOS troposphere step can only be
    # evaluated over a box that stays inside that grid. A widened box (the 18 km
    # one we'd need to reach Huruma's Karura anchor, ~8 km N) overran the GACOS
    # grid and crashed tropo_gacos. We therefore keep the 2 km box and let
    # mintpy_run snap a LOCAL coherent+stable anchor instead of distant Karura —
    # see reference_* below, which are set to the snapped pixel.
    processing_side_m=None,
    phenomenon="informal_settlement_subsidence",
    footprint_source="open_buildings",
    n_synthetic_buildings=1200,
    narrative=(
        "South C ward at the Kiganjo / Muhoho Avenue junction (Plot 209/5909/10, "
        "near South C Shopping Centre and Nairobi South Hospital) — site of the "
        "2 Jan 2026 pancake collapse of a mixed-use tower built to 14–16 floors "
        "against a 12-floor approval and standing stop-development notices. Used "
        "here as an honest retrospective: the failure was structural overload on "
        "an active construction site (unauthorised extra floors), a mechanism "
        "InSAR cannot see directly — it measures ground/surface deformation, not "
        "a building's load-vs-capacity margin. A pre-collapse reclassification "
        "(series truncated to <2 Jan 2026, validated to reproduce the pipeline "
        "exactly) returns INSUFFICIENT_EVIDENCE / danger=STABLE for every "
        "building within 80 m of the site, which is the truthful outcome — the "
        "system declines to certify what it cannot measure. Note the verdict is "
        "NOT driven by low coherence: these pixels are high-coherence (γ≈0.86, "
        "AOI mean 0.94). It is driven by the defensibility gate — the velocity σ "
        "(≈0.7 mm/yr) exceeds the AOI's own stable-bulk noise floor (σ p75 "
        "≈0.33 mm/yr) AND the trend is not linear (R²≈0.57 < 0.70 floor) — so a "
        "small, noisy, non-linear ground signal is honestly declared "
        "untrustworthy rather than reported as a threat. Footprints are "
        "ML-derived (Google Open Buildings) and resolve only the 1–2 storey "
        "fabric around the plot, not the tower itself. Shares Huruma's ascending "
        "Sentinel-1 frame, anchored to a local coherent-stable reference inside "
        "the tile."
    ),
    # The actual SBAS zero MintPy snapped to: the median-velocity pixel of the
    # coherent stable bulk (γ≈0.96), near the N edge of the 26×25 grid. Pinned
    # here so the map ⚓ shows the true reference, not a nominal placeholder.
    reference_lon=36.83523,
    reference_lat=-1.30511,
    # South C's tile moves near-uniformly (no internal stable ground) and Karura
    # is out of GACOS coverage here → no external anchor; velocities are de-meaned
    # to the coherent-bulk median in join_insar and read as differential.
    external_anchor=False,
    reference_note=(
        "Local coherent-stable reference inside the South C tile (snapped by "
        "MintPy to a high-coherence, near-zero-velocity, clustered pixel). South "
        "C lies at the southern edge of the GACOS grid, so the shared Karura "
        "bedrock anchor (used by the other Nairobi AOIs) is out of tropospheric "
        "coverage here; velocities are anchored to this in-tile stable zero."
    ),
)


REGISTRY: list[AOI] = [HURUMA, MOMBASA, KILELESHWA, KILIMANI, SOUTH_C]


def by_code(code: str) -> AOI:
    for a in REGISTRY:
        if a.code == code:
            return a
    raise KeyError(f"unknown AOI: {code}")


def _square_bbox(center_lon: float, center_lat: float, side_m: float) -> tuple[float, float, float, float]:
    """(minlon, minlat, maxlon, maxlat) for a square of `side_m` — equirect approx."""
    import math
    half = side_m / 2.0
    dlat = half / 111_320.0
    dlon = half / (111_320.0 * math.cos(math.radians(center_lat)))
    return (
        center_lon - dlon, center_lat - dlat,
        center_lon + dlon, center_lat + dlat,
    )


def bbox(aoi: AOI) -> tuple[float, float, float, float]:
    """Display bbox — the 2 km tile the UI and bundle show (`side_m`)."""
    return _square_bbox(aoi.center_lon, aoi.center_lat, aoi.side_m)


def processing_bbox(aoi: AOI) -> tuple[float, float, float, float]:
    """MintPy load/clip bbox — `processing_side_m` if set, else the display bbox.

    Wider than `bbox` so it can contain the reference anchor (which sits outside
    the display tile) and give every interferogram one identical clip extent.
    """
    side = aoi.processing_side_m if aoi.processing_side_m is not None else aoi.side_m
    return _square_bbox(aoi.center_lon, aoi.center_lat, side)
