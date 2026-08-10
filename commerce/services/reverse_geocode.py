"""Reverse-geocode a coord to a Nairobi-metro neighbourhood (§8 Chunk C+).

One public function: ``reverse_geocode(db, lat, lng) -> str | None`` returning the display
name of the smallest / highest-priority neighbourhood rectangle containing the point, or
``None`` if the point is outside every seeded rectangle. The caller (the /live-viewers
endpoint) uses ``None`` to fall back to a generic "Nairobi" label.

Seeded on boot from ``_SEED_NEIGHBOURHOODS`` — a static tuple of ~30 areas. ``ensure_seeded``
is idempotent (INSERT OR IGNORE per slug); calling it twice is a no-op. Adding a new area is
a code change to the seed, not a migration.

Rectangles are axis-aligned bounding boxes in WGS84 degrees, sourced from public
OpenStreetMap-ish knowledge of Nairobi neighbourhoods. They ARE approximate — a rectangle
over Kilimani will clip parts of Kileleshwa and vice versa. For "which suburb is the
seller's viewer in" this is accurate enough; the priority column tie-breaks overlaps in
favor of the more specific area.

The rectangle around all of Nairobi metro (slug='nairobi', priority=1000) is a catch-all so
a lat/lng anywhere in the Nairobi bounding box that misses every specific rectangle still
resolves to "Nairobi" rather than None. Outside that big box we return None; the caller
decides what to say (probably "Kenya" or the empty string).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from PE.commerce.models.neighbourhood import Neighbourhood


@dataclass(frozen=True)
class _SeedRow:
    slug: str
    name: str
    min_lat: float
    max_lat: float
    min_lng: float
    max_lng: float
    priority: int


# The seed set — ~30 neighbourhoods in Nairobi + immediate suburbs, plus one catch-all
# "Nairobi metro" rectangle at very low priority. Coordinates are hand-drawn axis-aligned
# rectangles over OpenStreetMap; a viewer inside multiple boxes resolves to the lowest-
# priority (most specific) name.
#
# Priority conventions:
#   * 10 — a specific suburb (Kilimani, Karen, Juja, etc.)
#   * 100 — a broader area (default from the model)
#   * 1000 — the metro catch-all ("Nairobi")
#
# If two suburbs overlap, the tie-break is (priority ASC, slug ASC). Adjust priorities
# rather than trimming rectangles when refining coverage.
_SEED_NEIGHBOURHOODS: tuple[_SeedRow, ...] = (
    # Inner Nairobi & north
    _SeedRow("cbd",         "Nairobi CBD",   -1.295, -1.276, 36.815, 36.835, 10),
    _SeedRow("westlands",   "Westlands",     -1.275, -1.253, 36.795, 36.820, 10),
    _SeedRow("parklands",   "Parklands",     -1.263, -1.245, 36.815, 36.840, 10),
    _SeedRow("gigiri",      "Gigiri",        -1.240, -1.220, 36.795, 36.830, 10),
    _SeedRow("muthaiga",    "Muthaiga",      -1.258, -1.240, 36.825, 36.855, 10),
    _SeedRow("runda",       "Runda",         -1.220, -1.190, 36.795, 36.830, 10),
    # South / west inner
    _SeedRow("kilimani",    "Kilimani",      -1.298, -1.278, 36.775, 36.800, 10),
    _SeedRow("kileleshwa",  "Kileleshwa",    -1.285, -1.265, 36.775, 36.800, 10),
    _SeedRow("lavington",   "Lavington",     -1.290, -1.265, 36.755, 36.780, 10),
    _SeedRow("karen",       "Karen",         -1.335, -1.300, 36.680, 36.735, 10),
    _SeedRow("langata",     "Langata",       -1.365, -1.335, 36.735, 36.780, 10),
    # South of the CBD
    _SeedRow("south-b",     "South B",       -1.315, -1.295, 36.825, 36.850, 10),
    _SeedRow("south-c",     "South C",       -1.320, -1.300, 36.810, 36.835, 10),
    _SeedRow("industrial",  "Industrial Area", -1.315, -1.290, 36.840, 36.870, 10),
    # East
    _SeedRow("eastleigh",   "Eastleigh",     -1.290, -1.265, 36.850, 36.875, 10),
    _SeedRow("buruburu",    "Buruburu",      -1.290, -1.270, 36.870, 36.895, 10),
    _SeedRow("umoja",       "Umoja",         -1.295, -1.275, 36.895, 36.920, 10),
    _SeedRow("donholm",     "Donholm",       -1.300, -1.280, 36.890, 36.915, 10),
    _SeedRow("kayole",      "Kayole",        -1.290, -1.265, 36.910, 36.940, 10),
    _SeedRow("embakasi",    "Embakasi",      -1.335, -1.305, 36.870, 36.910, 10),
    _SeedRow("utawala",     "Utawala",       -1.290, -1.270, 36.940, 36.980, 10),
    # North-east
    _SeedRow("kasarani",    "Kasarani",      -1.235, -1.210, 36.880, 36.920, 10),
    _SeedRow("roysambu",    "Roysambu",      -1.225, -1.205, 36.870, 36.900, 10),
    _SeedRow("kahawa",      "Kahawa",        -1.205, -1.170, 36.900, 36.945, 10),
    # Extended metro (Kiambu / Machakos side)
    _SeedRow("ruiru",       "Ruiru",         -1.180, -1.135, 36.930, 36.985, 10),
    _SeedRow("juja",        "Juja",          -1.115, -1.070, 37.010, 37.075, 10),
    _SeedRow("thika",       "Thika",         -1.055, -1.010, 37.055, 37.115, 10),
    _SeedRow("kikuyu",      "Kikuyu",        -1.270, -1.235, 36.635, 36.685, 10),
    _SeedRow("ngong",       "Ngong",         -1.395, -1.345, 36.635, 36.690, 10),
    _SeedRow("rongai",      "Ongata Rongai", -1.410, -1.375, 36.720, 36.775, 10),
    _SeedRow("kitengela",   "Kitengela",     -1.510, -1.455, 36.930, 37.000, 10),
    _SeedRow("athi-river",  "Athi River",    -1.480, -1.430, 36.960, 37.020, 10),
    # Catch-all (lowest priority = highest number)
    _SeedRow("nairobi",     "Nairobi",       -1.530, -1.150, 36.600, 37.120, 1000),
)


def ensure_seeded(db: Session) -> None:
    """Idempotent: INSERT-if-not-exists every row in ``_SEED_NEIGHBOURHOODS``. Runs on boot
    (see ``core/database.create_tables``) and in every test fixture. Cheap — ~30 upserts of
    small rows, one round-trip per row on SQLite; a bulk INSERT ... ON CONFLICT on PostgreSQL
    when we want it later.

    Uses ``INSERT OR IGNORE`` (SQLite) / ``ON CONFLICT DO NOTHING`` (PostgreSQL) so a
    concurrent boot on another process doesn't fight us. Both dialects treat the primary-key
    conflict as the natural "already there" signal.
    """
    if db.bind.dialect.name == "postgresql":
        stmt = text(
            "INSERT INTO neighbourhoods (slug, name, min_lat, max_lat, min_lng, max_lng, priority) "
            "VALUES (:slug, :name, :min_lat, :max_lat, :min_lng, :max_lng, :priority) "
            "ON CONFLICT (slug) DO NOTHING"
        )
    else:
        # SQLite. Both syntaxes work on newer SQLite too, but INSERT OR IGNORE is the more
        # universal SQLite idiom.
        stmt = text(
            "INSERT OR IGNORE INTO neighbourhoods "
            "(slug, name, min_lat, max_lat, min_lng, max_lng, priority) "
            "VALUES (:slug, :name, :min_lat, :max_lat, :min_lng, :max_lng, :priority)"
        )
    for row in _SEED_NEIGHBOURHOODS:
        db.execute(stmt, {
            "slug": row.slug, "name": row.name,
            "min_lat": row.min_lat, "max_lat": row.max_lat,
            "min_lng": row.min_lng, "max_lng": row.max_lng,
            "priority": row.priority,
        })
    db.commit()


def reverse_geocode(db: Session, lat: float, lng: float) -> Optional[str]:
    """Return the display name of the highest-priority (i.e. lowest ``priority`` value)
    neighbourhood rectangle containing (lat, lng), or None if the point is outside every
    seeded rectangle.

    Query: ``WHERE min_lat <= lat AND max_lat >= lat AND min_lng <= lng AND max_lng >= lng
    ORDER BY priority ASC, slug ASC LIMIT 1``. Uses the (min_lat, max_lat) index; the
    remaining two columns prune. Sub-millisecond on ~30 rows regardless of dialect.

    Guards against bogus coords: NaN / infinite lat|lng returns None rather than raising
    (the SQL layer would reject them, but we intercept earlier for a clean "no idea" answer).
    """
    if lat is None or lng is None:
        return None
    if lat != lat or lng != lng:   # NaN check (NaN != NaN is a JS-ism that also works in py)
        return None
    if not (-90.0 <= lat <= 90.0 and -180.0 <= lng <= 180.0):
        return None

    row = (
        db.query(Neighbourhood)
        .filter(
            Neighbourhood.min_lat <= lat,
            Neighbourhood.max_lat >= lat,
            Neighbourhood.min_lng <= lng,
            Neighbourhood.max_lng >= lng,
        )
        .order_by(Neighbourhood.priority.asc(), Neighbourhood.slug.asc())
        .limit(1)
        .one_or_none()
    )
    return row.name if row is not None else None
