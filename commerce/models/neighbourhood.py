"""Nairobi-metro neighbourhood reverse-geocode table (§8 Chunk C+).

A row per named neighbourhood, storing a rectangular bounding box (min_lat/max_lat +
min_lng/max_lng). At runtime the reverse-geocode probe is a two-column BETWEEN query — a
btree seek on ``min_lat`` gets us candidates, and the remaining three fields prune.
Rectangles ARE a rough approximation (real neighbourhoods aren't axis-aligned), but for
the "which suburb are you in" bucket the seller sees, they're accurate enough and they
avoid the full PostGIS polygon-in-polygon machinery.

Not modeling as a PostGIS Geography Polygon on purpose:
  * A rectangle is a two-BETWEEN filter — dialect-agnostic (works on PostGIS AND SQLite
    with zero branching), and the query planner picks a btree scan for free.
  * The set is static (~30 rows), seeded on boot; adding a new area is a data change, not
    a schema change. If we later need real polygons we'll add a ``geog`` column beside the
    rectangle; existing readers still work.

A given lat/lng may match MULTIPLE rectangles (overlaps between neighbouring boxes are
inevitable — Kilimani ⊂ some larger box). The service resolves that with a stable
per-neighbourhood ``priority`` (lower wins); a smaller/more-specific area is given a
lower priority than a big catch-all like "Nairobi metro".
"""
from __future__ import annotations

from sqlalchemy import Column, Float, Index, Integer, String

from PE.commerce.core.database import Base


class Neighbourhood(Base):
    """One named area, defined by a rectangular bounding box."""
    __tablename__ = "neighbourhoods"

    # Slug key ('kilimani', 'south-c'). Also the primary key — we don't need a uuid; the slug
    # is human-readable, stable, and unique. Cap at 40 chars (a display name can be longer;
    # 'name' below carries the display form).
    slug = Column(String(40), primary_key=True)
    # The label surfaced to a viewer ("South C", "Nairobi CBD"). Never rendered from slug.
    name = Column(String(80), nullable=False)
    # Rectangular bounding box in WGS84 degrees.
    min_lat = Column(Float, nullable=False)
    max_lat = Column(Float, nullable=False)
    min_lng = Column(Float, nullable=False)
    max_lng = Column(Float, nullable=False)
    # Tie-breaker when a coord falls into multiple overlapping rectangles. Lower priority wins,
    # so a specific area (Kilimani = 10) beats a catch-all (Nairobi metro = 100).
    priority = Column(Integer, nullable=False, default=100)

    __table_args__ = (
        # Point-in-rectangle probe: WHERE min_lat <= ? AND max_lat >= ? AND min_lng <= ? AND max_lng >= ?
        # A btree on (min_lat, max_lat) drives the first pass; the remaining two columns prune.
        # Not a spatial GiST — a rectangle table with ~30 rows doesn't warrant it.
        Index("ix_neighbourhoods_bbox", "min_lat", "max_lat"),
    )
