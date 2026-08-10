"""InSAR deep-link session endpoint (the identity half of the InSAR↔Weespas bridge).

The InSAR risk-map SPA is stateless — no login, no session, no identity (work_flow.md
§9.1). To meter commercial usage of it WITHOUT bolting auth onto InSAR, Weespas mints a
short-lived telemetry-scoped token here and hands it to the InSAR frontend on deep-link.
InSAR replays that token on POST /insar-telemetry/event; the scorer then sees building
views / exports under the right user_id (commercial_model.md §7, work_flow.md §9.4).

This endpoint is the ONE place the token is created, and only ever for the caller
themselves (get_current_user). Anonymous callers never reach here — individuals are free
anyway, so telemetry simply stays off for them (the Weespas frontend opens InSAR plain).
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from redis.exceptions import RedisError
from sqlalchemy.orm import Session

from PE.weespas.core.config import settings
from PE.weespas.core.database import get_db
from PE.weespas.models.user import User, UserRole
from PE.weespas.models.insar_link import BuildingLink, BuildingLinkCandidate
from PE.weespas.services import commerce_read_client, contact_service, entitlement_service, insar_resolver
from PE.weespas.services import event_bus, structural_flag_service
from PE.weespas.services.auth_service import (
    create_insar_telemetry_token,
    get_current_user,
    get_current_user_optional,
    require_insar_telemetry_token,
    verify_property_ownership,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/insar", tags=["insar"])


class InsarSession(BaseModel):
    token: str
    insar_url: str
    aoi_code: Optional[str] = None
    building_id: Optional[int] = None


class InsarAccess(BaseModel):
    ok: bool = True


class ListingRisk(BaseModel):
    """The honest coverage badge for a listing (work_flow.md §9.3 Option B).

    `coverage` is one of monitored / needs_confirmation / monitored_land / not_monitored
    / unavailable — the cardinal rule is that 'unknown' or 'outside coverage' is NEVER
    reported as 'safe'. When monitored, `danger_level` (0=STABLE … 4=CRITICAL) +
    `match_confidence` travel with it. When `provisional` is True (needs_confirmation),
    `danger_level` is the WORST-case tier among the candidate buildings — a conservative
    placeholder shown until the owner taps the right building, never a confirmed reading.
    """
    coverage: str
    danger_level: Optional[int] = None
    aoi_code: Optional[str] = None
    # The resolved InSAR building id (when monitored). Lets a certifier's flag-entry
    # UI target the exact building behind a listing without hand-typing the id. Not
    # sensitive — it's an opaque footprint index, already public on the InSAR map.
    insar_building_id: Optional[int] = None
    match_method: Optional[str] = None
    match_confidence: Optional[float] = None
    # True ⇒ danger_level is a worst-case placeholder for an unconfirmed clustered pin.
    provisional: bool = False
    # How many candidate buildings the pin could be (drives the "confirm" prompt).
    candidate_count: Optional[int] = None


@router.get("/verify", response_model=InsarAccess)
def verify(_sub: str = Depends(require_insar_telemetry_token)) -> InsarAccess:
    """Gatekeeper for the InSAR map UI.

    InSAR is now free-but-login-required (commercial_model.md): no anonymous access.
    The stateless InSAR SPA calls this on load with the telemetry-scoped token it was
    handed on deep-link; a VALID, unexpired, correctly-scoped token returns 200 and the
    map renders. Anything else — no token, a forged/expired token, or a normal access
    token (rejected by the dep because it lacks the telemetry scope) — yields 401, and
    InSAR bounces the visitor to the Weespas login.

    O(1): the dep only verifies the JWT signature + scope, no DB load. This gates the
    map UI, not the public read API (backend/app/main.py is intentionally untouched).
    """
    return InsarAccess(ok=True)


def _resolve_listing(db: Session, listing_id: str) -> tuple[Optional[str], Optional[int]]:
    """Map a listing to its InSAR (aoi, building) for the fly-to deep-link.

    O(1) hot path: read the persisted BuildingLink first (indexed on listing_id —
    most viewed listings were already linked in P4a). Only on a miss do we fall back
    to a live spatial resolve, which also persists the link for next time. A listing
    outside coverage / with no address simply yields (None, None) → nav-level link.
    """
    link = (
        db.query(BuildingLink)
        .filter(BuildingLink.listing_id == listing_id)
        .first()
    )
    if link is not None:
        return link.aoi_code, int(link.insar_building_id)

    # Lazy fallback: resolve from the listing's address coordinates if we have them.
    from PE.weespas.models.property import Property  # local import avoids a cycle

    prop = db.query(Property).filter(Property.id == listing_id).first()
    if prop is None or prop.address is None:
        return None, None
    try:
        category_slug = prop.category.slug if prop.category is not None else None
        result = insar_resolver.resolve_and_link(
            db,
            listing_id=listing_id,
            lat=float(prop.address.latitude),
            lon=float(prop.address.longitude),
            category=category_slug, title=prop.title, description=prop.description,
            size_numeric=prop.size_numeric,
        )
    except Exception as exc:  # resolver is best-effort; never fail the deep-link
        logger.warning("insar resolve failed for listing %s: %s", listing_id, exc)
        return None, None
    if result.coverage == insar_resolver.COVERAGE_MONITORED:
        return result.aoi_code, result.insar_building_id
    return None, None


@router.get("/session-token", response_model=InsarSession)
def session_token(
    listing_id: Optional[str] = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> InsarSession:
    """Mint a telemetry-scoped token + the InSAR deep-link for the signed-in user.

    With `listing_id`, resolves the listing→building so InSAR can fly to it
    ("View on risk map"); without it, returns a nav-level link ("Risk Map").
    """
    role_value = user.role.value if isinstance(user.role, UserRole) else (user.role or "user")
    token = create_insar_telemetry_token(user.id, role_value)

    aoi_code: Optional[str] = None
    building_id: Optional[int] = None
    if listing_id:
        aoi_code, building_id = _resolve_listing(db, listing_id)

    return InsarSession(
        token=token,
        insar_url=settings.insar_public_url,
        aoi_code=aoi_code,
        building_id=building_id,
    )


@router.get("/listing/{listing_id}/risk", response_model=ListingRisk)
def listing_risk(
    listing_id: str,
    db: Session = Depends(get_db),
    _user: Optional[User] = Depends(get_current_user_optional),
) -> ListingRisk:
    """The listing's InSAR coverage badge (work_flow.md §9.8 #3a / §9.3 Option B).

    Public-read (the coarse coverage/tier of a public listing is not sensitive — it
    mirrors 'free for individuals'; fine-grained per-building diagnostics stay gated).
    Optional auth only so a signed-in caller is attributable, never required.

    Hot path is O(1): read the persisted BuildingLink (indexed on listing_id), then a
    single indexed tier lookup for the CURRENT danger level (the link is stable but the
    tier is re-scored on every rebuild, so it must be read live — never cached stale).
    On a link miss, fall back ONCE to a spatial resolve that also persists the link for
    next time. Any failure degrades to 'unavailable' — NEVER to 'safe' (the §9.7 rule).
    """
    link = (
        db.query(BuildingLink)
        .filter(BuildingLink.listing_id == listing_id)
        .first()
    )
    try:
        if link is not None:
            result = insar_resolver.tier_for_building(
                link.aoi_code, int(link.insar_building_id)
            )
            # Confidence is a property of how the link was made, not of the tier read;
            # carry the stored link confidence through so a 'nearest' match stays honest.
            return ListingRisk(
                coverage=result.coverage,
                danger_level=result.danger_level,
                aoi_code=result.aoi_code,
                insar_building_id=int(link.insar_building_id),
                match_method=link.match_method,
                match_confidence=link.match_confidence,
            )

        # No authoritative link. A clustered (ambiguous) pin leaves a candidate set but no
        # link — surface its conservative worst-case tier as PROVISIONAL (read live), so the
        # listing is never under-stated while it waits to be confirmed.
        prov = insar_resolver.provisional_tier_for_candidates(db, listing_id)
        if prov.coverage == insar_resolver.COVERAGE_NEEDS_CONFIRMATION:
            return ListingRisk(
                coverage=prov.coverage,
                danger_level=prov.danger_level,
                provisional=True,
                candidate_count=prov.candidate_count,
            )

        # No link and no candidates yet — resolve from the address coordinates (persisting
        # the link/candidate set for next time), passing attributes for disambiguation.
        from PE.weespas.models.property import Property  # local import avoids a cycle

        prop = db.query(Property).filter(Property.id == listing_id).first()
        if prop is None or prop.address is None:
            return ListingRisk(coverage=insar_resolver.COVERAGE_NOT_MONITORED)
        category_slug = prop.category.slug if prop.category is not None else None
        result = insar_resolver.resolve_and_link(
            db,
            listing_id=listing_id,
            lat=float(prop.address.latitude),
            lon=float(prop.address.longitude),
            category=category_slug, title=prop.title, description=prop.description,
            size_numeric=prop.size_numeric,
        )
        return ListingRisk(
            coverage=result.coverage,
            danger_level=result.danger_level,
            aoi_code=result.aoi_code,
            insar_building_id=result.insar_building_id,
            match_method=result.match_method,
            match_confidence=result.match_confidence,
            provisional=result.provisional,
            candidate_count=result.candidate_count,
        )
    except Exception as exc:  # resolver is best-effort; a failure is 'unavailable', not 'safe'
        logger.warning("listing risk resolve failed for %s: %s", listing_id, exc)
        return ListingRisk(coverage=insar_resolver.COVERAGE_UNAVAILABLE)


# Cap the batch so a caller can't ask for an unbounded IN(...) — one screen of
# listings is well under this.
_CONFIRMED_BATCH_MAX = 200


class ConfirmedRequest(BaseModel):
    listing_ids: list[str] = Field(default_factory=list, max_length=_CONFIRMED_BATCH_MAX)


class ConfirmedResponse(BaseModel):
    # listing_id -> True iff its building has a recorded on-the-ground assessment.
    confirmed: dict[str, bool]


@router.post("/listings/confirmed", response_model=ConfirmedResponse)
def listings_confirmed(
    body: ConfirmedRequest,
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
) -> ConfirmedResponse:
    """Batch "is this listing's building ground-confirmed?" — one query for a whole page
    of listings (no N+1), for the ✅ shield on the agent's listing cards.

    Auth-gated. Returns ONLY a boolean per listing ("a human has assessed this
    building"), never the flag's content/source/state — so it stays clear of the
    sensitive unsafe-flag↔listing join (work_flow.md §4.2/§9.7). Every requested id is
    present in the response (False when not confirmed), so the caller needs no
    reconciliation.
    """
    ids = body.listing_ids[:_CONFIRMED_BATCH_MAX]
    confirmed = structural_flag_service.confirmed_listing_ids(db, ids)
    return ConfirmedResponse(confirmed={lid: (lid in confirmed) for lid in ids})


# --------------------------------------------------------------- shops on the InSAR map (§8.1a)
# The marquee "the map IS the proof of proximity" feature: which building footprints in an AOI
# are commerce shops, so the stateless InSAR SPA can pin them (and glow a confirmed one). The
# InSAR FE has NO commerce token — it holds only this telemetry-scoped token — so weespas
# AGGREGATES: it owns the BuildingLink spine (property_uuid ↔ insar_building_id) and the
# StructuralFlag "second sensor", and mints a short-lived read:feed commerce token S2S to fetch
# the shop display-meta. A shop's raw coordinates NEVER cross here (S6): the footprint the client
# already renders IS the location, so the response carries only building_id + non-PII shop meta.


class ShopOnMap(BaseModel):
    """One shop pinned to a building the InSAR bundle already contains. ``insar_building_id`` is
    the bundle key the FE looks up (O(1)); no lat/lng — the footprint is the location."""
    property_uuid: str
    insar_building_id: int
    shop_id: str
    name: str
    category: Optional[str] = None
    # PER-BUILDING ground-confirmed provenance (a recorded structural assessment exists). This is
    # provenance, NOT a safety claim — same honest meaning as the listing-card shield.
    confirmed: bool = False


class ShopsOnMapResponse(BaseModel):
    aoi_code: str
    shops: list[ShopOnMap] = Field(default_factory=list)
    # True when the commerce read could not be completed (unreachable/slow/inert-bridge) and the
    # shop layer is therefore INCOMPLETE. The map still renders (never goes dark on a commerce
    # hiccup — a subsidence tool must not); the FE can surface a subtle "shops unavailable" hint.
    partial: bool = False


@router.get("/shops/near", response_model=ShopsOnMapResponse)
def shops_near(
    aoi: str = Query(..., min_length=1, max_length=64),
    db: Session = Depends(get_db),
    _sub: str = Depends(require_insar_telemetry_token),
) -> ShopsOnMapResponse:
    """Which building footprints in ``aoi`` are shops (§8.1a). Telemetry-scoped: the same gate as
    /insar/verify — a normal access token (no telemetry scope) is 401, so this is reachable only
    by the InSAR SPA's own token.

    Pipeline (all bounded/indexed): (1) the AOI's BuildingLinks → (property_uuid, building_id)
    pairs, hard-capped; (2) S2S read:feed call to commerce for which of those property_uuids are
    shops; (3) per-building confirmed set from StructuralFlag. Coordinates never leave commerce.

    Graceful degradation (decided in the plan): if the commerce read fails, return the shops list
    EMPTY with ``partial=true`` and log it — the map renders exactly as today rather than erroring."""
    # Per-sub throttle: each call fans out one bounded S2S read into commerce, so cap how fast a
    # single signed-in account can drive it (SECURITY.md §3/§9 posture on the weespas plane). O(1),
    # fail-open on a Redis error — auth already gated us; a Redis blip must not blind the risk map.
    if not entitlement_service.check_rate_limit(
        "shops_on_map", _sub,
        max_hits=settings.shops_on_map_rate_max,
        window_seconds=settings.shops_on_map_rate_window_s,
    ):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many requests. Please slow down.")
    links = structural_flag_service.aoi_building_links(
        db, aoi, cap=settings.commerce_shops_aoi_link_cap
    )
    if not links:
        # No linked buildings in this AOI — nothing to pin, no commerce call.
        return ShopsOnMapResponse(aoi_code=aoi, shops=[], partial=False)

    # property_uuid may map to several buildings and vice-versa; keep the full pairing so a
    # shop on a shared/duplicated footprint is not lost. Batch de-dup for the commerce call.
    property_uuids = list(dict.fromkeys(lid for lid, _bid in links))

    try:
        # Least-privilege S2S: role "user" — a read:feed call needs no elevated role, and the
        # telemetry token intentionally carries no role we should trust for authorization.
        raw_shops = commerce_read_client.shops_by_property(_sub, "user", property_uuids)
    except commerce_read_client.CommerceReadError as e:
        # Degrade, don't fail: the map is a life-safety surface and must not go dark because
        # commerce is slow/down/inert. Log for the telemetry; return no pins + partial flag.
        logger.warning("shops-on-map commerce read failed for aoi=%s: %s", aoi, e)
        return ShopsOnMapResponse(aoi_code=aoi, shops=[], partial=True)

    # Index the shop meta by property_uuid (one uuid → possibly several shops).
    shops_by_uuid: dict[str, list[dict]] = {}
    for s in raw_shops:
        puid = s.get("property_uuid")
        if isinstance(puid, str):
            shops_by_uuid.setdefault(puid, []).append(s)
    if not shops_by_uuid:
        # Linked buildings exist but none are shops — a complete, empty-of-shops answer.
        return ShopsOnMapResponse(aoi_code=aoi, shops=[], partial=False)

    # Per-building confirmed provenance — only for buildings that actually carry a shop.
    building_ids = [bid for lid, bid in links if lid in shops_by_uuid]
    confirmed = structural_flag_service.confirmed_building_ids(db, aoi, building_ids)

    pins: list[ShopOnMap] = []
    for listing_id, building_id in links:
        for s in shops_by_uuid.get(listing_id, ()):  # 0..n shops on this footprint
            shop_id = s.get("shop_id")
            name = s.get("name")
            if not isinstance(shop_id, str) or not isinstance(name, str):
                continue  # skip a malformed row rather than emit a broken pin
            pins.append(ShopOnMap(
                property_uuid=listing_id,
                insar_building_id=building_id,
                shop_id=shop_id,
                name=name,
                category=s.get("category"),
                confirmed=building_id in confirmed,
            ))
    return ShopsOnMapResponse(aoi_code=aoi, shops=pins, partial=False)


# --------------------------------------------------------------------------- confirm flow
# The "bad pin" disambiguation: when a clustered pin can't be auto-resolved, the listing
# owner taps the right building. These two endpoints power that flow.


class Candidate(BaseModel):
    insar_building_id: int
    aoi_code: str
    distance_m: Optional[float] = None
    height_m: Optional[float] = None
    n_floors: Optional[int] = None
    danger_level: Optional[int] = None   # LIVE tier (re-read), 0..4
    geometry: Optional[dict] = None      # footprint GeoJSON (already public on the InSAR map)


class CandidatesResponse(BaseModel):
    listing_id: str
    coverage: str
    provisional: bool = False
    candidates: list[Candidate] = Field(default_factory=list)


class ConfirmBuildingRequest(BaseModel):
    insar_building_id: int


def _owned_listing_or_403(db: Session, listing_id: str, user: User):
    """Load the listing and enforce owner/agent (admin bypass). 404 if it doesn't exist."""
    from PE.weespas.models.property import Property  # local import avoids a cycle

    prop = db.query(Property).filter(Property.id == listing_id).first()
    if prop is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "listing not found")
    verify_property_ownership(user, prop)  # raises 403 for a non-owner
    return prop


@router.get("/listing/{listing_id}/candidates", response_model=CandidatesResponse)
def listing_candidates(
    listing_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> CandidatesResponse:
    """The plausible footprints a listing's pin could be, for the tap-to-confirm UI.

    Owner/agent only (NOT public, unlike /risk): it exposes several nearby footprint
    geometries + tiers, which only the listing owner needs. Returns each candidate with its
    LIVE tier (re-read) + footprint outline — never structural-flag content, never the
    unsafe↔listing join. One read-only spatial query for the whole set (no N+1).
    """
    _owned_listing_or_403(db, listing_id, user)
    cands = insar_resolver.candidates_with_geometry(db, listing_id)
    provisional = (
        db.query(BuildingLinkCandidate)
        .filter(BuildingLinkCandidate.listing_id == listing_id,
                BuildingLinkCandidate.vetoed.is_(False))
        .first()
    ) is not None and (
        db.query(BuildingLink).filter(BuildingLink.listing_id == listing_id).first() is None
    )
    return CandidatesResponse(
        listing_id=listing_id,
        coverage=(insar_resolver.COVERAGE_NEEDS_CONFIRMATION if provisional
                  else insar_resolver.COVERAGE_MONITORED if cands
                  else insar_resolver.COVERAGE_NOT_MONITORED),
        provisional=provisional,
        candidates=[Candidate(**c) for c in cands],
    )


@router.post("/listing/{listing_id}/confirm", response_model=ListingRisk)
def confirm_listing_building(
    listing_id: str,
    body: ConfirmBuildingRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ListingRisk:
    """Persist the owner's building choice as an AUTHORITATIVE link, then return its tier.

    Owner/agent only. The chosen building MUST be one of the listing's stored candidates —
    an arbitrary id is rejected (400) so a listing can never be pointed at an unrelated,
    safer-looking building. Idempotent; flips the listing to 'monitored'.
    """
    prop = _owned_listing_or_403(db, listing_id, user)

    candidate = (
        db.query(BuildingLinkCandidate)
        .filter(BuildingLinkCandidate.listing_id == listing_id,
                BuildingLinkCandidate.insar_building_id == body.insar_building_id,
                BuildingLinkCandidate.vetoed.is_(False))
        .first()
    )
    if candidate is None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "that building is not one of this listing's candidates",
        )

    result = insar_resolver.confirm_building(
        db, listing_id=listing_id,
        aoi_code=candidate.aoi_code, building_id=body.insar_building_id,
    )
    # Reflect the now-confirmed monitored state on the listing row.
    from PE.weespas.models.property import VERIFICATION_MONITORED
    from datetime import datetime, timezone

    prop.verification_status = VERIFICATION_MONITORED
    prop.verified_at = datetime.now(timezone.utc)
    db.commit()

    return ListingRisk(
        coverage=result.coverage,
        danger_level=result.danger_level,
        aoi_code=result.aoi_code,
        insar_building_id=body.insar_building_id,
        match_method=insar_resolver.METHOD_AGENT_CONFIRMED,
        match_confidence=1.0,
    )


# --------------------------------------------------------------------------- §8.1b pair-radiate
# "POST to act, SSE to hear." The DOWNLINK half: a long-lived Server-Sent-Events stream over which
# a user receives the anonymized "a viewer is looking at your shop" pulses addressed to them. The
# uplink (POST /insar/contact) publishes those pulses. See services/event_bus.py for the rail.


@router.get("/contact/stream")
async def contact_stream(
    request: Request,
    _sub: str = Depends(require_insar_telemetry_token),
) -> StreamingResponse:
    """Subscribe to this user's OWN pair-radiate channel as an SSE stream (§8.1b downlink).

    Telemetry-gated exactly like /insar/shops/near — reachable only by the InSAR SPA's own token,
    and the channel is keyed on the verified ``sub`` so a caller can only ever hear their OWN
    events (no channel is caller-supplied — there is no cross-user subscription to attempt).

    The stream emits one ``data:`` frame per event plus a periodic ``:keep-alive`` comment, and is
    torn down cleanly on client disconnect or after ``contact_sse_max_connection_s`` (the client
    transparently reconnects — a closed tab therefore leaks no subscriber slot). No DB work, no
    body: a pure realtime pipe. ``X-Accel-Buffering: no`` disables nginx response buffering so
    frames flush immediately (SECURITY.md/§5 note); ``Cache-Control: no-cache`` keeps proxies from
    caching the stream."""
    stream = event_bus.sse_subscribe(
        contact_service.channel_for(_sub),
        is_disconnected=request.is_disconnected,
        heartbeat_s=settings.contact_sse_heartbeat_s,
        max_connection_s=settings.contact_sse_max_connection_s,
    )
    return StreamingResponse(
        stream,
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


class ContactRequest(BaseModel):
    """A buyer opened a shop pin — the pair-radiate trigger. Carries only the shop opened and the
    footprint it sits on; the buyer's OWN footprints are resolved server-side from the token, never
    trusted from the client (a client can't be allowed to nominate which buildings 'it owns')."""
    shop_id: str = Field(..., min_length=1, max_length=64)
    aoi: str = Field(..., min_length=1, max_length=64)
    shop_building_id: int = Field(..., ge=0)


class ContactResponse(BaseModel):
    # The buyer's OWN footprints in this AOI, for the browser to glow locally (the consented half —
    # the buyer tapped). Empty for a plain buyer who owns no listings. NEVER the seller's identity.
    own_building_ids: list[int] = Field(default_factory=list)
    # Glow breath-then-fade window (seconds), so the FE and backend agree on the decay without a
    # "contact ended" message. The FE clamps this defensively.
    glow_ttl_s: int = 0
    # True once the anonymized pulse was accepted for delivery to the seller. False is NON-fatal
    # (seller offline / commerce inert / unknown shop) — the buyer-local glow still works.
    radiated: bool = False


@router.post("/contact", response_model=ContactResponse)
def contact(
    body: ContactRequest,
    db: Session = Depends(get_db),
    _sub: str = Depends(require_insar_telemetry_token),
) -> ContactResponse:
    """Register a pair-radiate contact and fan out its anonymized pulse (§8.1b uplink).

    Two independent halves, by privacy design (decision #2 — anonymized-on-browse):
      * BUYER half (always works): resolve the caller's OWN footprints in ``aoi`` from the verified
        token and return them so the buyer's browser glows the buildings it owns — locally, from
        THIS response, never an SSE self-loop. A buyer who owns nothing gets an empty list.
      * SELLER half (best-effort): publish an ANONYMIZED ``{shop_building_id, aoi}`` pulse to the
        shop owner's per-user channel so their map glows the shop. The buyer's building_ids NEVER
        leave for the seller (no home-location leak); the seller learns only "a viewer is looking".

    Telemetry-gated + per-sub rate-limited (each call does one S2S seller lookup + one publish — a
    bounded amplifier, throttled like shops-on-map; O(1) Redis, fail-open, auth is the real control).
    The ``shop_building_id`` is verified to be a real linked footprint before it is echoed/published,
    so a forged id can't make a seller glow an arbitrary building. The seller lookup / publish are
    best-effort: any failure degrades to ``radiated=false`` and still returns the buyer glow — a
    contact must never error just because the other party is offline or commerce is inert."""
    if not entitlement_service.check_rate_limit(
        "insar_contact", _sub,
        max_hits=settings.contact_rate_max,
        window_seconds=settings.contact_rate_window_s,
    ):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many requests. Please slow down.")

    # Authenticity: the pin must correspond to a real linked footprint in this AOI. A stale/forged
    # id is not an error for the buyer (they still glow their own buildings) but must NOT be
    # published to a seller — so gate only the radiate half on it.
    footprint_ok = contact_service.shop_footprint_exists(db, body.aoi, body.shop_building_id)

    own = contact_service.viewer_building_ids_in_aoi(
        db, _sub, body.aoi, cap=settings.contact_footprint_cap
    )

    radiated = False
    if footprint_ok:
        try:
            # least-privilege S2S: role "user" — a read:feed lookup needs no elevated role.
            seller_uuid = commerce_read_client.seller_uuid_for_shop(_sub, "user", body.shop_id)
        except commerce_read_client.CommerceReadError as e:
            logger.warning("contact: seller lookup failed for shop=%s: %s", body.shop_id, e)
            seller_uuid = None
        # Don't self-radiate: if the "buyer" IS the seller (owner opening their own pin), skip the
        # pulse — it would just glow their own map for no reason.
        if seller_uuid and seller_uuid != _sub:
            try:
                # Anonymized payload ONLY (decision #2): shop footprint + AOI, never buyer ids.
                delivered = event_bus.publish_sync(
                    contact_service.channel_for(seller_uuid),
                    {"kind": "contact", "shop_building_id": body.shop_building_id, "aoi": body.aoi},
                )
                radiated = delivered > 0
            except RedisError:
                logger.warning("contact: pulse publish failed for shop=%s", body.shop_id, exc_info=True)

    return ContactResponse(
        own_building_ids=own,
        glow_ttl_s=settings.contact_glow_ttl_s,
        radiated=radiated,
    )
