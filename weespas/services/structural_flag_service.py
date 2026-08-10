"""Structural-flag service — records the engineer/authority "second sensor".

A `professional` (civil/geo engineer) or an `authority` records a structural
judgement for a specific InSAR building: CLEARED / UNSAFE / AUTH_UNSAFE. This is
the input InSAR is physically blind to (construction quality). The row is written
to `structural_flag`; the InSAR build later reads the most-recent flag per building
and fuses it into the collapse score (see InSAR scripts/structural_flags.py).

This is the MANUAL-entry path. An automatic NCA/enforcement feed lands on the same
`record_flag` seam later — same validation, same table — so no schema change is
needed to add it.

Safety rules enforced here (not just in the route):
  - state must be a known FLAG_* value, and recording NONE is rejected (a flag means
    a judgement was made; "uninspected" is the absence of a row, not a row of NONE).
  - AUTH_UNSAFE (authority condemnation) may only be set by an AUTHORITY/STAFF/ADMIN
    — a professional cannot self-declare an authority-grade enforcement notice.
  - source must match the actor class (engineer ⇄ professional; authority ⇄ authority).
NO notification is dispatched here (that is P4c); this only records the input.
"""
from __future__ import annotations

from datetime import date

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from PE.weespas.models.insar_link import (
    BuildingLink, StructuralFlag,
    FLAG_NONE, FLAG_CLEARED, FLAG_UNSAFE, FLAG_AUTH_UNSAFE,
    VALID_FLAG_STATES, VALID_FLAG_SOURCES,
)
from PE.weespas.models.user import User, UserRole
from PE.weespas.services import flag_review_service

# States that actually represent a recorded judgement (NONE is the absence of one).
_RECORDABLE_STATES = {FLAG_CLEARED, FLAG_UNSAFE, FLAG_AUTH_UNSAFE}


def record_flag(
    db: Session,
    *,
    actor: User,
    aoi_code: str,
    insar_building_id: int,
    state: int,
    source: str,
    observed_at: date | None = None,
    note: str | None = None,
) -> StructuralFlag:
    """Validate and persist one structural flag. Returns the created row.

    Raises 400 on invalid state/source, 403 if the actor isn't allowed to set the
    requested state (only an authority can set AUTH_UNSAFE).
    """
    if state not in VALID_FLAG_STATES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            f"invalid state {state!r}; expected one of {sorted(VALID_FLAG_STATES)}")
    if state == FLAG_NONE or state not in _RECORDABLE_STATES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "cannot record state NONE — a flag must be CLEARED, UNSAFE, or AUTH_UNSAFE")
    if source not in VALID_FLAG_SOURCES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            f"invalid source {source!r}; expected one of {list(VALID_FLAG_SOURCES)}")

    is_authority = actor.has_role(UserRole.AUTHORITY) or actor.has_role(UserRole.STAFF) \
        or actor.has_role(UserRole.ADMIN)

    # Only an authority (or staff/admin acting as one) may issue an authority-grade
    # condemnation. A professional engineer's strongest flag is UNSAFE.
    if state == FLAG_AUTH_UNSAFE and not is_authority:
        raise HTTPException(status.HTTP_403_FORBIDDEN,
                            "only an authority may set AUTH_UNSAFE (authority condemnation)")
    if source == "authority" and not is_authority:
        raise HTTPException(status.HTTP_403_FORBIDDEN,
                            "source 'authority' requires the authority role")

    flag = StructuralFlag(
        aoi_code=aoi_code,
        insar_building_id=int(insar_building_id),
        state=int(state),
        observed_at=observed_at,
        source=source,
        note=note,
        granted_by=actor.id,
    )
    db.add(flag)
    # Flush so flag.id is populated, then open the staff/admin review alert in the SAME
    # transaction — the alert is atomic with the flag (a downed worker can never lose
    # it, because there is no worker; see flag_review_service). The future automated
    # NCA feed lands on this same seam and gets the alert for free.
    db.flush()
    flag_review_service.create_for_flag(db, flag)
    db.commit()
    db.refresh(flag)
    return flag


def latest_flag_for_building(
    db: Session, *, aoi_code: str, insar_building_id: int
) -> StructuralFlag | None:
    """Most-recent flag for a building (what the InSAR build would fuse), or None."""
    return (
        db.query(StructuralFlag)
        .filter(
            StructuralFlag.aoi_code == aoi_code,
            StructuralFlag.insar_building_id == int(insar_building_id),
        )
        .order_by(StructuralFlag.created_at.desc())
        .first()
    )


def aoi_building_links(
    db: Session, aoi_code: str, *, cap: int
) -> list[tuple[str, int]]:
    """The building links in one AOI, as ``(listing_id, insar_building_id)`` pairs (§8.1a —
    shops on the InSAR map). ``listing_id`` is the synchronized ``property_uuid`` a commerce
    shop carries; ``insar_building_id`` is the footprint the map already renders.

    ONE indexed query (``idx_building_link_aoi_building`` leads on ``aoi_code``) and HARD-CAPPED
    at ``cap`` rows (anti-O(n), S8) — an AOI has far fewer linked buildings than the cap, so the
    limit is a backstop, not a truncation in practice. Ordered by ``insar_building_id`` so the
    cap, if it ever bites, is deterministic (never a random subset). Empty AOI ⇒ empty list."""
    rows = (
        db.query(BuildingLink.listing_id, BuildingLink.insar_building_id)
        .filter(BuildingLink.aoi_code == aoi_code)
        .order_by(BuildingLink.insar_building_id)
        .limit(cap)
        .all()
    )
    return [(lid, int(bid)) for lid, bid in rows]


def confirmed_building_ids(
    db: Session, aoi_code: str, building_ids: list[int]
) -> set[int]:
    """Of the given buildings in one AOI, which have a recorded on-the-ground assessment (any
    structural flag, state != NONE) — the PER-BUILDING "ground-confirmed" set for the map shield.

    Distinct from ``confirmed_listing_ids`` (per-listing): a pin is a specific building, so the
    shield must reflect THAT footprint's assessment, never over-state "Confirmed" on an unflagged
    building that merely shares a listing. ONE indexed query on ``structural_flag`` (its
    ``idx_structural_flag_building_created`` leads on ``insar_building_id``), scoped to the AOI +
    the requested buildings. Empty input ⇒ empty set (no query). Returns membership only — never
    the flag content/source — so it is safe to surface to a browser (work_flow.md §4.2/§9.7)."""
    if not building_ids:
        return set()
    rows = (
        db.query(StructuralFlag.insar_building_id)
        .filter(
            StructuralFlag.aoi_code == aoi_code,
            StructuralFlag.insar_building_id.in_(building_ids),
            StructuralFlag.state != FLAG_NONE,
        )
        .distinct()
        .all()
    )
    return {int(r[0]) for r in rows}


def confirmed_listing_ids(db: Session, listing_ids: list[str]) -> set[str]:
    """Of the given listings, which map to a building that has a recorded on-the-ground
    assessment (any structural flag, state != NONE) — i.e. is "ground-confirmed".

    ONE indexed query (no N+1): join building_link → structural_flag on
    (aoi_code, insar_building_id), restricted to the requested listing_ids and to real
    judgements (state != FLAG_NONE), returning the DISTINCT listing_ids that have ≥1
    flag. Empty input ⇒ empty set (no query). Returns only a boolean-by-membership set —
    never the flag content/source — so it is safe to surface to a listing owner/browser.
    """
    if not listing_ids:
        return set()
    rows = (
        db.query(BuildingLink.listing_id)
        .join(
            StructuralFlag,
            (StructuralFlag.aoi_code == BuildingLink.aoi_code)
            & (StructuralFlag.insar_building_id == BuildingLink.insar_building_id),
        )
        .filter(
            BuildingLink.listing_id.in_(listing_ids),
            StructuralFlag.state != FLAG_NONE,
        )
        .distinct()
        .all()
    )
    return {r[0] for r in rows}
