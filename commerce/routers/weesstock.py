"""WeesStock market router (§WeesStock F4) — the investor-facing discovery/analytics surface.

DISCOVERY/ANALYTICS ONLY. Nothing here transacts, takes money, or issues an instrument; a
future investment action lives behind a separate, clearly-labelled, regulatory-aware surface
(Kenya: Capital Markets (Investment-Based Crowdfunding) Regulations 2022). This router is the
honest read layer: which consenting shops are trading, how much verified money moved, and the
trend.

Auth model:
  * GET /weesstock/markets and /weesstock/markets/{seller_id} — ANY authenticated commerce
    principal (audience scope only, NOT create:trades): investors browse the market with the
    same token class as buyers reading the feed. Commerce fails closed (no token-less reads).
  * POST /weesstock/me/listing — the seller's OWN consent switch; requires create:trades and
    is resolved from the token ``sub`` (no id parameter — there is no way to consent on
    someone else's behalf).

Consent is the exposure boundary (S6): the list returns consenting sellers only, and the
detail view 404s for BOTH an unknown id and an unlisted seller — probing must never confirm
that an unlisted seller exists. Payloads are aggregates only: no buyer identities, no
per-order lines (the F4 privacy shape).
"""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from PE.commerce.core.auth import CommercePrincipal, get_current_principal, require_scope
from PE.commerce.core.database import get_db
from PE.commerce.schemas import weesstock as weesstock_schemas
from PE.commerce.schemas import weesstock_market as market_schemas
from PE.commerce.services import credit_score, weesstock_market

router = APIRouter(tags=["weesstock"])

# The consent switch is a seller write — same granular scope as every other seller mutation.
_require_write = require_scope("create:trades")

# Uniform 404 message: an investor probing seller ids must never learn whether an unlisted
# seller exists, and a seller without a shop has nothing to list.
_NOT_FOUND = "Market entry not found"


@router.get("/weesstock/me/listing", response_model=market_schemas.ListingToggleOut)
def get_my_listing(
    db: Session = Depends(get_db),
    principal: CommercePrincipal = Depends(_require_write),
) -> market_schemas.ListingToggleOut:
    """The seller's own current WeesStock market consent — the READ half of the switch.

    Owner-only by construction (resolved from the token ``sub``), so it can only ever read
    the caller's own flag. Same uniform 404 as the write half for a caller with no seller
    row. Returns the current state so the UI can render the switch from the server, never
    from a stale client guess.
    """
    listed = weesstock_market.get_listed(db, principal.sub)
    if listed is None:
        raise HTTPException(status_code=404, detail=_NOT_FOUND)
    return market_schemas.ListingToggleOut(listed=listed)


@router.post("/weesstock/me/listing", response_model=market_schemas.ListingToggleOut)
def set_my_listing(
    body: market_schemas.ListingToggleIn,
    db: Session = Depends(get_db),
    principal: CommercePrincipal = Depends(_require_write),
) -> market_schemas.ListingToggleOut:
    """The seller's own WeesStock market consent switch (opt-in, default off).

    Owner-only by construction: the seller row is resolved from the verified token ``sub``,
    so there is no way to list (or unlist) anyone else. A caller with no seller row gets the
    uniform 404 — they have no shop to offer investors.
    """
    listed = weesstock_market.set_listed(db, principal.sub, body.listed)
    if listed is None:
        raise HTTPException(status_code=404, detail=_NOT_FOUND)
    return market_schemas.ListingToggleOut(listed=listed)


@router.get("/weesstock/markets", response_model=market_schemas.MarketListOut)
def markets(
    db: Session = Depends(get_db),
    _principal: CommercePrincipal = Depends(get_current_principal),
) -> market_schemas.MarketListOut:
    """The WeesStock market — every CONSENTING seller's ticker row, strongest-first.

    Bounded (service cap), deterministic ordering, and only opt-in sellers ever appear.
    Investors read this with the same authenticated audience token as the feed; there is no
    token-less path (commerce fails closed).
    """
    now = datetime.now(timezone.utc)
    entries = weesstock_market.list_markets(db, now=now)
    return market_schemas.MarketListOut(
        entries=entries,
        window_days=credit_score.REVENUE_WINDOW_DAYS,
        revenue_saturation_cents=credit_score.REVENUE_SATURATION_CENTS,
    )


@router.get("/weesstock/markets/{seller_id}", response_model=market_schemas.MarketDetailOut)
def market_detail(
    seller_id: str,
    db: Session = Depends(get_db),
    _principal: CommercePrincipal = Depends(get_current_principal),
) -> market_schemas.MarketDetailOut:
    """One shop's full WeesStock deep-dive (the investor detail page).

    Consent-gated: an UNLISTED seller's id 404s exactly like an unknown one — the API never
    confirms an unlisted seller exists. The profile is the SAME shape the seller sees on their
    own card, so the investor and the seller can never be told different numbers.
    """
    result = weesstock_market.market_detail(db, seller_id, now=datetime.now(timezone.utc))
    if result is None:
        raise HTTPException(status_code=404, detail=_NOT_FOUND)
    seller_meta, profile, series = result
    return market_schemas.MarketDetailOut(
        seller=seller_meta,
        profile=weesstock_schemas.to_credit_profile_out(profile),
        series=series,
    )
