"""§8.1b pair-radiate — resolve a viewer to their OWN building footprints in an AOI.

When a buyer opens a shop pin on the InSAR map, both the shop footprint and the buyer's own
footprint(s) radiate. This service answers the buyer half: "which building_ids in this AOI does
the viewer own?" — so the POST /insar/contact response can tell the buyer's browser which of its
already-rendered footprints to glow (the buyer consented by tapping; the glow is driven locally
from the POST response, never an SSE self-loop).

Ownership follows the shipped spine: telemetry ``sub`` == weespas ``User.id`` → ``User.agent_id``
→ ``Property.agent_id`` → ``BuildingLink`` (listing_id, aoi_code) → ``insar_building_id``. A plain
buyer with no listings (``agent_id`` NULL) owns no footprints and resolves to an empty list — the
common case, entirely fine (the buyer then glows only the shop pin).

The OTHER half — shop_id → owning-seller channel — is NOT here: it is a commerce S2S read
(``commerce_read_client.seller_uuid_for_shop``, the §8.1b seam built in the commerce chunk). The
router orchestrates both, exactly as the shops-on-map aggregator does. No coordinates ever cross
this service — only building_ids the map already renders (S6 / work_flow.md §9.7).
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from PE.weespas.models.insar_link import BuildingLink
from PE.weespas.models.property import Property
from PE.weespas.models.user import User

# The per-recipient Pub/Sub channel a user's SSE stream subscribes to and the contact uplink
# publishes to. Centralised here so the publisher (POST /insar/contact) and the subscriber
# (GET /insar/contact/stream) can never drift to different channel names. Per-user (never
# per-AOI) so nothing about who else is on the map ever leaks (design decision #2).
_CHANNEL_PREFIX = "contact-events:"


def channel_for(user_id: str) -> str:
    """The Pub/Sub channel carrying pair-radiate pulses addressed to ``user_id``."""
    return f"{_CHANNEL_PREFIX}{user_id}"


def viewer_building_ids_in_aoi(
    db: Session, viewer_sub: str, aoi_code: str, *, cap: int
) -> list[int]:
    """The ``insar_building_id``s in ``aoi_code`` owned by the viewer (telemetry ``sub``).

    ONE indexed query joining ``User → Property → BuildingLink`` on ``agent_id`` / ``listing_id``
    and scoped to the AOI. Because SQL ``NULL`` never equals ``NULL``, a viewer with no ``agent_id``
    (a buyer who owns no listings) matches no rows and yields ``[]`` — no special-casing needed.

    DISTINCT (a footprint shared across a viewer's own listings glows once) and HARD-CAPPED at
    ``cap`` rows ordered by ``insar_building_id`` (anti-O(n), S8) so a pathological account can never
    build an unbounded set and a bite is deterministic. Missing viewer / empty AOI ⇒ ``[]``."""
    if not viewer_sub or not aoi_code:
        return []
    rows = (
        db.query(BuildingLink.insar_building_id)
        .join(Property, Property.id == BuildingLink.listing_id)
        .join(User, User.agent_id == Property.agent_id)
        .filter(User.id == viewer_sub, BuildingLink.aoi_code == aoi_code)
        .distinct()
        .order_by(BuildingLink.insar_building_id)
        .limit(cap)
        .all()
    )
    return [int(r[0]) for r in rows]


def shop_footprint_exists(db: Session, aoi_code: str, building_id: int) -> bool:
    """True if ``building_id`` is a real linked footprint in ``aoi_code`` (§8.1b authenticity check).

    A shop pin the buyer can open is, by construction, a building with a ``BuildingLink`` (that is
    how shops-on-map finds it). Confirming the link exists before we echo/publish the id stops a
    forged or stale ``building_id`` from making a seller glow an arbitrary footprint — ONE indexed
    existence query on ``idx_building_link_aoi_building`` (leads on ``aoi_code``), O(1)."""
    if not aoi_code:
        return False
    return (
        db.query(BuildingLink.id)
        .filter(BuildingLink.aoi_code == aoi_code, BuildingLink.insar_building_id == building_id)
        .first()
    ) is not None
