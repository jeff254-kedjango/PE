"""Listing → InSAR-footprint resolver (attribute-aware, honest).

Resolves a Weespas listing's (lat, lon) to an InSAR building footprint and reports a
coverage state. The cardinal rule (a life-safety one): NEVER collapse "unknown / outside
coverage" into "safe". Absence of monitoring is its own state.

Coverage states:
  - "monitored"          : resolved to a single footprint we can stand behind; carries the
                           building's danger tier.
  - "needs_confirmation" : the pin landed in a CLUSTER of plausible footprints we can't
                           safely auto-pick. We do NOT guess (a wrong snap could show a
                           calm building's tier while the real one next door is CRITICAL).
                           Until the owner taps the right one we show the worst-case tier
                           among the live candidates (conservative, labelled provisional).
  - "monitored_land"     : a `land` listing — no building footprint. Ground movement is
                           ESTIMATED from neighbouring monitored buildings (never a
                           building tier; land has no footprint of its own to read).
  - "not_monitored"      : outside every AOI, or no plausible footprint. We invent nothing.
  - "unavailable"        : the InSAR DB isn't configured/readable. Also NOT "safe".

The "bad pin" problem: a dropped pin in dense areas sits near ~9 footprints (measured).
Pure nearest-neighbour picks the wrong one too often. So we gather ALL footprints in a
buffer and re-rank them by distance + attribute plausibility (category floor prior, loose
area for houses). Listing TEXT ("penthouse", "5th floor") is VETO-ONLY — it eliminates a
footprint too short to contain the unit but NEVER promotes a match toward a taller/safer
building (anti-gaming, see insar_text_signals). When ranking still can't separate the top
candidates, the listing is `needs_confirmation` rather than a silent wrong link.

Read-only against InSAR's DuckDB; one short-lived connection per resolve (the build swaps
the file atomically, so a fresh connection always sees consistent data).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from sqlalchemy.orm import Session

from PE.weespas.core.config import settings
from PE.weespas.models.insar_link import BuildingLink, BuildingLinkCandidate
from PE.weespas.services.insar_text_signals import FloorSignal, parse_floor_signals

# Degrees-per-metre at the equator (~111.32 km/deg). Nairobi/Mombasa are near the equator
# so latitude scaling is negligible at our radii; the flat approximation is deliberate —
# slightly over-tight (conservative) and trig-free. ST_Distance on lon/lat returns degrees.
_DEG_PER_M = 1.0 / 111_320.0

# Typical metres per storey — used only to DERIVE a floor count when n_floors is missing.
_M_PER_FLOOR = 3.2

# Hard cap on candidates pulled from one spatial query (a pathological cluster guard).
_SPATIAL_LIMIT = 64

COVERAGE_MONITORED = "monitored"
COVERAGE_NEEDS_CONFIRMATION = "needs_confirmation"
COVERAGE_MONITORED_LAND = "monitored_land"
COVERAGE_NOT_MONITORED = "not_monitored"
COVERAGE_UNAVAILABLE = "unavailable"

# Match methods recorded on BuildingLink (mirror models/insar_link comment).
METHOD_PIP = "pip"
METHOD_DISAMBIGUATED = "disambiguated"
METHOD_AGENT_CONFIRMED = "agent_confirmed"
METHOD_LAND_AGGREGATE = "land_aggregate"

# --- Category policy (Axis A: should this listing map to a building at all?) ---
_POLICY_LAND = "land"          # never snaps; ground estimated from neighbours
_POLICY_INFORMAL = "informal"  # kiosk/container/stall — a not_monitored is honest
_POLICY_BUILDING = "building"  # full disambiguation

_INFORMAL_CATEGORIES = {"kiosk", "container", "stall"}

# Category → (min_floors, typical_floors, multi_floor_likely). A PRIOR only (it shapes the
# score), never a hard gate — hard elimination is the veto step. Used to prefer the
# footprint whose height matches what the category usually looks like.
_FLOOR_PRIOR = {
    "apartment":        (2, 6, True),
    "office":           (1, 5, True),
    "studio":           (1, 4, True),
    "commercial_space": (1, 3, True),
    "villa":            (1, 2, False),
    "house":            (1, 2, False),
    "shop":             (1, 2, False),
    "warehouse":        (1, 1, False),
    "other":            (1, 2, False),
}
_DEFAULT_PRIOR = (1, 2, False)

# Categories whose unit area ≈ building footprint area (so size_numeric is a usable nudge).
# Apartments etc. are excluded on purpose — a 120 m² flat sits in a 400 m² block ("area trap").
_AREA_CATEGORIES = {"house", "villa"}

# Score weights — distance dominates (most reliable), plausibility breaks ties, area nudges.
_W_DIST, _W_FLOOR, _W_AREA = 0.6, 0.3, 0.1


@dataclass
class Candidate:
    """One footprint near the pin, with the features the scorer/veto need + a snapshot
    of its tier (snapshot is for persistence/debugging only — tier is re-read live)."""
    aoi_code: str
    building_id: int
    danger_level: Optional[int]
    floors: int                  # resolved (real n_floors, else derived from height), >=1
    floors_imputed: bool         # the height/floor was imputed → down-weight the prior
    height_m: Optional[float]
    distance_m: float
    area_m2: Optional[float]
    contains: bool               # point-in-polygon (authoritative containment)
    score: float = 0.0
    vetoed: bool = False


@dataclass
class ResolveResult:
    coverage: str                              # one of the COVERAGE_* constants
    aoi_code: Optional[str] = None
    insar_building_id: Optional[int] = None
    danger_level: Optional[int] = None         # tier when monitored; worst-case when provisional
    match_method: Optional[str] = None
    match_confidence: Optional[float] = None
    provisional: bool = False                   # True ⇒ danger_level is a worst-case placeholder
    candidate_count: Optional[int] = None
    land_ground_band: Optional[int] = None      # land only: worst neighbour tier (NOT a building tier)
    land_neighbor_count: Optional[int] = None
    candidates: list[Candidate] = field(default_factory=list)  # to persist (not serialized out)


# --------------------------------------------------------------------------- helpers

def _connect():
    """Open a read-only DuckDB connection to the InSAR DB, or None if not configured.

    Imported lazily so Weespas has no hard dependency on duckdb unless the integration is
    actually enabled.
    """
    path = settings.insar_duckdb_path
    if not path:
        return None
    try:
        import duckdb
    except ImportError:
        return None
    try:
        con = duckdb.connect(path, read_only=True)
        con.execute("INSTALL spatial; LOAD spatial")
        return con
    except Exception:
        return None


def match_policy_for_category(category: Optional[str]) -> str:
    """Axis A gate. Unknown/None → BUILDING (full disambiguation, safest default)."""
    if category == "land":
        return _POLICY_LAND
    if category in _INFORMAL_CATEGORIES:
        return _POLICY_INFORMAL
    return _POLICY_BUILDING


def expected_floors(category: Optional[str]) -> tuple[int, int, bool]:
    return _FLOOR_PRIOR.get(category or "", _DEFAULT_PRIOR)


def _max_danger(cands: list[Candidate]) -> Optional[int]:
    """Worst (highest) danger tier among candidates; None if none carry a tier."""
    tiers = [c.danger_level for c in cands if c.danger_level is not None]
    return max(tiers) if tiers else None


def _derive_floors(n_floors, height_m, fused_height_m) -> int:
    if n_floors and int(n_floors) > 0:
        return int(n_floors)
    h = fused_height_m if fused_height_m else height_m
    if h and h > 0:
        return max(1, round(float(h) / _M_PER_FLOOR))
    return 1


# --------------------------------------------------------------------------- scoring (pure)

def rank_candidates(
    candidates: list[Candidate],
    category: Optional[str],
    floor_signal: FloorSignal,
    size_numeric: Optional[float],
    *,
    buffer_radius_m: float,
) -> tuple[list[Candidate], bool]:
    """Apply veto + score the candidates. PURE (no DB) so it is unit-testable.

    Returns (live_sorted, veto_to_empty): `live_sorted` are the non-vetoed candidates
    sorted best-first; `veto_to_empty` is True when text vetoes removed EVERY candidate
    (in which case the veto is rolled back — text must never erase the whole candidate
    set; a human resolves it instead). Mutates each candidate's `.score`/`.vetoed`.
    """
    min_exp, typical, _multi = expected_floors(category)

    # --- Veto pass (hard elimination, never promotion) ---
    has_taller = any(c.floors > 1 for c in candidates)
    for c in candidates:
        if floor_signal.min_required_floors > c.floors:
            c.vetoed = True            # a 5th-floor unit cannot be in a 3-floor footprint
        elif floor_signal.penthouse and c.floors <= 1 and has_taller:
            c.vetoed = True            # a penthouse cannot be a single-storey building when a taller one is right here

    live = [c for c in candidates if not c.vetoed]
    veto_to_empty = bool(candidates) and not live
    if veto_to_empty:
        # Text contradicts ALL geometry — do not erase the set; un-veto and let a human pick.
        for c in candidates:
            c.vetoed = False
        live = list(candidates)

    # --- Score pass (only the live candidates matter for the decision) ---
    floor_scale = max(typical * 1.5, 3.0)
    for c in live:
        dist_term = max(0.0, 1.0 - (c.distance_m / buffer_radius_m)) if buffer_radius_m > 0 else 0.0
        floor_term = max(0.0, 1.0 - abs(c.floors - typical) / floor_scale)
        if c.floors_imputed:
            floor_term *= 0.5          # an imputed height must never dominate the ranking
        area_term = _area_term(category, c.area_m2, size_numeric)
        c.score = round(_W_DIST * dist_term + _W_FLOOR * floor_term + _W_AREA * area_term, 6)

    live.sort(key=lambda c: c.score, reverse=True)
    return live, veto_to_empty


def _area_term(category: Optional[str], area_m2: Optional[float], size_numeric: Optional[float]) -> float:
    """Loose footprint-vs-unit-area agreement — house/villa ONLY (the area trap rule).

    size_numeric units are ambiguous (UI says sqft, model comment says sqm), so this is a
    gentle tiebreak (weight 0.1), never a gate: within ~2× → full, decaying beyond.
    """
    if category not in _AREA_CATEGORIES or not size_numeric or not area_m2 or size_numeric <= 0:
        return 0.0
    ratio = max(area_m2, size_numeric) / min(area_m2, size_numeric)
    if ratio <= 2.0:
        return 1.0
    return max(0.0, 1.0 - (ratio - 2.0) / 4.0)


# --------------------------------------------------------------------------- spatial gather

def _gather_candidates(con, lat: float, lon: float, radius_m: float) -> list[Candidate]:
    radius_deg = radius_m * _DEG_PER_M
    rows = con.execute(
        """
        SELECT aoi_code, building_id, danger_level, height_m, n_floors, fused_height_m,
               height_imputed,
               ST_Distance(geom, ST_Point(?, ?)) AS dist_deg,
               ST_Area(geom) AS area_deg2,
               ST_Contains(geom, ST_Point(?, ?)) AS contains
        FROM buildings
        WHERE ST_DWithin(geom, ST_Point(?, ?), ?)
        ORDER BY dist_deg
        LIMIT ?
        """,
        [lon, lat, lon, lat, lon, lat, radius_deg, _SPATIAL_LIMIT],
    ).fetchall()
    out: list[Candidate] = []
    for (aoi, bid, danger, height, n_floors, fused_h, h_imp, dist_deg, area_deg2, contains) in rows:
        out.append(Candidate(
            aoi_code=aoi,
            building_id=int(bid),
            danger_level=int(danger) if danger is not None else None,
            floors=_derive_floors(n_floors, height, fused_h),
            floors_imputed=bool(h_imp),
            height_m=float(fused_h) if fused_h else (float(height) if height else None),
            distance_m=(float(dist_deg) / _DEG_PER_M) if dist_deg is not None else float("inf"),
            area_m2=(float(area_deg2) / (_DEG_PER_M * _DEG_PER_M)) if area_deg2 is not None else None,
            contains=bool(contains),
        ))
    return out


# --------------------------------------------------------------------------- resolve

def resolve_point(
    lat: float,
    lon: float,
    *,
    category: Optional[str] = None,
    title: Optional[str] = None,
    description: Optional[str] = None,
    size_numeric: Optional[float] = None,
) -> ResolveResult:
    """Resolve a coordinate (+ optional listing attributes) to a coverage decision.

    No persistence — `resolve_and_link` wraps this and stores the result.
    """
    con = _connect()
    if con is None:
        return ResolveResult(coverage=COVERAGE_UNAVAILABLE)
    try:
        policy = match_policy_for_category(category)
        if policy == _POLICY_LAND:
            return _resolve_land(con, lat, lon)

        cands = _gather_candidates(con, lat, lon, settings.insar_buffer_radius_m)
        if not cands:
            return ResolveResult(coverage=COVERAGE_NOT_MONITORED)

        floor_signal = parse_floor_signals(title, description)
        live, veto_to_empty = rank_candidates(
            cands, category, floor_signal, size_numeric,
            buffer_radius_m=settings.insar_buffer_radius_m,
        )
        pip = next((c for c in cands if c.contains), None)

        # 1) Authoritative containment, unless text explicitly contradicts it.
        if pip is not None and not pip.vetoed:
            return ResolveResult(
                coverage=COVERAGE_MONITORED,
                aoi_code=pip.aoi_code, insar_building_id=pip.building_id,
                danger_level=pip.danger_level, match_method=METHOD_PIP,
                match_confidence=1.0, candidate_count=len(cands), candidates=cands,
            )

        # 2) A contained footprint that text rules out → don't auto-pick a safer one; confirm.
        if pip is not None and pip.vetoed:
            return _needs_confirmation(live or cands)

        # 3) Veto wiped everything (text vs geometry) → human resolves.
        if veto_to_empty:
            return _needs_confirmation(cands)

        # 4) Informal categories: only accept a clearly-close footprint; else honestly none.
        if policy == _POLICY_INFORMAL and live[0].distance_m > settings.insar_buffer_radius_m * 0.5:
            return ResolveResult(coverage=COVERAGE_NOT_MONITORED)

        # 5) Single live candidate → auto-link.
        if len(live) == 1:
            return _monitored(live[0], live, METHOD_DISAMBIGUATED)

        # 6) Two-way ambiguity → confirm; clear winner → auto-link.
        if (live[0].score - live[1].score) < settings.insar_ambiguity_score_gap:
            return _needs_confirmation(live)
        return _monitored(live[0], live, METHOD_DISAMBIGUATED)
    finally:
        con.close()


def _monitored(winner: Candidate, candidates: list[Candidate], method: str) -> ResolveResult:
    return ResolveResult(
        coverage=COVERAGE_MONITORED,
        aoi_code=winner.aoi_code, insar_building_id=winner.building_id,
        danger_level=winner.danger_level, match_method=method,
        match_confidence=winner.score, candidate_count=len(candidates), candidates=candidates,
    )


def _needs_confirmation(candidates: list[Candidate]) -> ResolveResult:
    """Ambiguous: no auto-link, show the WORST-case tier among candidates as provisional."""
    return ResolveResult(
        coverage=COVERAGE_NEEDS_CONFIRMATION,
        danger_level=_max_danger(candidates), provisional=True,
        candidate_count=len(candidates), candidates=candidates,
    )


def _resolve_land(con, lat: float, lon: float) -> ResolveResult:
    """`land` listings have no footprint. Estimate the ground band from neighbouring
    monitored buildings within the land-aggregate radius (a parcel shares its neighbours'
    ground field). The headline is the WORST neighbour tier (conservative). NEVER a
    building danger_level — `land_ground_band` is a distinct, clearly-labelled field.
    """
    cands = _gather_candidates(con, lat, lon, settings.insar_land_aggregate_radius_m)
    if not cands:
        return ResolveResult(coverage=COVERAGE_NOT_MONITORED)
    # Rank land neighbours by proximity (no attribute scoring — they're not "the" building).
    cands.sort(key=lambda c: c.distance_m)
    return ResolveResult(
        coverage=COVERAGE_MONITORED_LAND,
        land_ground_band=_max_danger(cands),
        land_neighbor_count=len(cands),
        candidate_count=len(cands),
        candidates=cands,
    )


def tier_for_building(aoi_code: str, building_id: int) -> ResolveResult:
    """Fetch the CURRENT danger tier for an already-linked building (no spatial join).

    The link (aoi, building_id) is stable, but the tier is NOT — a rebuild re-scores it (a
    structural flag can escalate it). So a cached link must still read the tier live. This
    is a single indexed point lookup on the buildings view (cheaper than the spatial
    resolve), keyed on the persisted ids.

    Returns MONITORED with the fresh tier on a hit; UNAVAILABLE if the InSAR DB is off;
    NOT_MONITORED if the building has vanished (an AOI dropped/re-scoped) — never 'safe'.
    """
    con = _connect()
    if con is None:
        return ResolveResult(coverage=COVERAGE_UNAVAILABLE)
    try:
        row = con.execute(
            """
            SELECT danger_level
            FROM buildings
            WHERE aoi_code = ? AND building_id = ?
            LIMIT 1
            """,
            [aoi_code, building_id],
        ).fetchone()
        if row is None:
            return ResolveResult(coverage=COVERAGE_NOT_MONITORED)
        return ResolveResult(
            coverage=COVERAGE_MONITORED,
            aoi_code=aoi_code,
            insar_building_id=building_id,
            danger_level=int(row[0]) if row[0] is not None else None,
            match_method="link",
            match_confidence=1.0,
        )
    finally:
        con.close()


def provisional_tier_for_candidates(db: Session, listing_id: str) -> ResolveResult:
    """Live worst-case tier for a `needs_confirmation` listing, from its stored candidates.

    Same discipline as `tier_for_building`: we cache only the candidate IDS and re-read the
    tier live (it is re-scored on every InSAR rebuild). Returns NEEDS_CONFIRMATION with the
    MAX live danger_level among non-vetoed candidates; NOT_MONITORED if the set is empty;
    UNAVAILABLE if the DB is off — never 'safe'.
    """
    rows = (
        db.query(BuildingLinkCandidate)
        .filter(BuildingLinkCandidate.listing_id == listing_id,
                BuildingLinkCandidate.vetoed.is_(False))
        .order_by(BuildingLinkCandidate.rank)
        .all()
    )
    if not rows:
        return ResolveResult(coverage=COVERAGE_NOT_MONITORED)
    con = _connect()
    if con is None:
        return ResolveResult(coverage=COVERAGE_UNAVAILABLE)
    try:
        clause, params = _in_clause(rows)
        live = con.execute(
            f"SELECT danger_level FROM buildings WHERE {clause}", params
        ).fetchall()
        tiers = [int(r[0]) for r in live if r[0] is not None]
        return ResolveResult(
            coverage=COVERAGE_NEEDS_CONFIRMATION,
            danger_level=max(tiers) if tiers else None,
            provisional=True,
            candidate_count=len(rows),
        )
    finally:
        con.close()


def candidates_with_geometry(db: Session, listing_id: str) -> list[dict]:
    """The non-vetoed candidates for a listing, enriched with LIVE tier + footprint GeoJSON.

    Powers the "confirm your building" UI. One read-only spatial query for the whole set
    (no N+1). Returns [] when there are no candidates or the DB is off. Exposes only the
    coarse tier + footprint outline (already public on the InSAR map) — never flag content.
    """
    import json

    rows = (
        db.query(BuildingLinkCandidate)
        .filter(BuildingLinkCandidate.listing_id == listing_id,
                BuildingLinkCandidate.vetoed.is_(False))
        .order_by(BuildingLinkCandidate.rank)
        .limit(settings.insar_max_candidates)
        .all()
    )
    if not rows:
        return []
    con = _connect()
    if con is None:
        return []
    try:
        clause, params = _in_clause(rows)
        live = con.execute(
            f"""SELECT aoi_code, building_id, danger_level, ST_AsGeoJSON(geom) AS gj
                FROM buildings WHERE {clause}""",
            params,
        ).fetchall()
    finally:
        con.close()
    by_key = {(a, int(b)): (d, gj) for (a, b, d, gj) in live}
    out: list[dict] = []
    for r in rows:
        d, gj = by_key.get((r.aoi_code, int(r.insar_building_id)), (None, None))
        out.append({
            "insar_building_id": int(r.insar_building_id),
            "aoi_code": r.aoi_code,
            "distance_m": r.distance_m,
            "height_m": r.height_m,
            "n_floors": r.n_floors,
            "danger_level": int(d) if d is not None else None,
            "geometry": json.loads(gj) if gj else None,
        })
    return out


def _in_clause(rows) -> tuple[str, list]:
    """Build an OR-of-(aoi,bid) WHERE clause + params for a candidate set (DB-agnostic)."""
    parts, params = [], []
    for r in rows:
        parts.append("(aoi_code = ? AND building_id = ?)")
        params.extend([r.aoi_code, int(r.insar_building_id)])
    return " OR ".join(parts), params


# --------------------------------------------------------------------------- persistence

def _persist_candidates(db: Session, listing_id: str, candidates: list[Candidate]) -> None:
    """Replace the listing's candidate set with the freshly-resolved one (delete + insert).

    Stores up to `insar_max_candidates`, already best-first. `danger_level_at_resolve` is a
    snapshot for debugging — readers re-read the tier live, never trusting this value.
    """
    db.query(BuildingLinkCandidate).filter(
        BuildingLinkCandidate.listing_id == listing_id
    ).delete(synchronize_session=False)
    for rank, c in enumerate(candidates[: settings.insar_max_candidates]):
        db.add(BuildingLinkCandidate(
            listing_id=listing_id,
            aoi_code=c.aoi_code,
            insar_building_id=c.building_id,
            rank=rank,
            score=c.score or None,
            distance_m=c.distance_m,
            height_m=c.height_m,
            n_floors=c.floors,
            danger_level_at_resolve=c.danger_level,
            vetoed=c.vetoed,
        ))


def resolve_and_link(
    db: Session,
    *,
    listing_id: str,
    lat: float,
    lon: float,
    category: Optional[str] = None,
    title: Optional[str] = None,
    description: Optional[str] = None,
    size_numeric: Optional[float] = None,
) -> ResolveResult:
    """Resolve a listing's coordinate and persist/refresh its BuildingLink + candidate set.

    A human-confirmed link is AUTHORITATIVE: if one exists we never re-resolve over it — we
    just return its live tier. Otherwise: an auto 'monitored' result (PIP or a clear
    disambiguation winner) creates/updates the link; 'needs_confirmation' / 'monitored_land'
    / 'not_monitored' do NOT write an authoritative link (a stale non-confirmed one is
    dropped) so no link reader can mistake an ambiguous state for a confirmed match. The
    candidate set is always refreshed for the confirm UI / provisional tier. 'unavailable'
    is transient — we touch nothing.
    """
    existing = (
        db.query(BuildingLink).filter(BuildingLink.listing_id == listing_id).first()
    )
    if existing is not None and existing.confirmed_by_agent:
        # Never overwrite a human confirmation. Return the confirmed building's live tier.
        res = tier_for_building(existing.aoi_code, int(existing.insar_building_id))
        res.match_method = existing.match_method
        res.match_confidence = existing.match_confidence
        return res

    result = resolve_point(
        lat, lon, category=category, title=title,
        description=description, size_numeric=size_numeric,
    )

    if result.coverage == COVERAGE_UNAVAILABLE:
        return result  # transient — keep whatever we already had

    _persist_candidates(db, listing_id, result.candidates)

    if result.coverage == COVERAGE_MONITORED:
        if existing is None:
            db.add(BuildingLink(
                listing_id=listing_id,
                aoi_code=result.aoi_code,
                insar_building_id=result.insar_building_id,
                match_method=result.match_method,
                match_confidence=result.match_confidence,
                candidate_count=result.candidate_count,
            ))
        else:
            existing.aoi_code = result.aoi_code
            existing.insar_building_id = result.insar_building_id
            existing.match_method = result.match_method
            existing.match_confidence = result.match_confidence
            existing.candidate_count = result.candidate_count
    elif existing is not None:
        # needs_confirmation / monitored_land / not_monitored: no authoritative link.
        db.delete(existing)

    db.commit()
    return result


def confirm_building(
    db: Session, *, listing_id: str, aoi_code: str, building_id: int,
) -> ResolveResult:
    """Persist a human's building choice as an AUTHORITATIVE link (method agent_confirmed).

    The caller (router) must already have verified ownership AND that (aoi, building_id) is
    one of the listing's stored candidates. Idempotent: re-confirming the same building just
    refreshes the live tier. Returns the confirmed building's current tier (MONITORED).
    """
    existing = (
        db.query(BuildingLink).filter(BuildingLink.listing_id == listing_id).first()
    )
    if existing is None:
        existing = BuildingLink(listing_id=listing_id)
        db.add(existing)
    existing.aoi_code = aoi_code
    existing.insar_building_id = building_id
    existing.match_method = METHOD_AGENT_CONFIRMED
    existing.match_confidence = 1.0
    existing.confirmed_by_agent = True
    db.commit()
    return tier_for_building(aoi_code, building_id)
