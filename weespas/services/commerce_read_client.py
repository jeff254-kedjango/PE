"""Thin server-to-server READ client into the commerce service (:8003).

The only weespas→commerce call path (§8.1a — shops on the InSAR map). Weespas is the token
authority, so it mints a short-lived, least-privilege ``read:feed`` commerce token for the
requesting user (auth_service.create_commerce_token) and replays it as a bearer — the exact
bridge the weespas frontend already uses, just server-side here.

Deliberately small and side-effect-free beyond the one HTTP call (mirrors mpesa_client): the
caller (routers/insar.py aggregator) owns the DB reads and the degrade-to-empty policy. This
module only knows how to ask commerce "which of these property_uuids are shops?" and to raise
``CommerceReadError`` on ANY failure (network, timeout, non-OK, malformed body) so the caller
can fall back to no-shop-pins rather than error the whole map.

Security: RS256 token minted per-call, least-privilege (read:feed only — never a write/money
scope), short-lived (commerce_token_ttl_min). Bounded timeout, NO retry (a subsidence map must
not amplify load on a struggling commerce). Ships INERT until RS256 keys land — the minted token
is HS256 in dev and commerce's RS256-only verifier rejects it, so the aggregator simply returns
no pins, exactly like every other bridge on this codebase.
"""
from __future__ import annotations

import logging

import requests

from PE.weespas.core.config import settings
from PE.weespas.services.auth_service import create_commerce_token

logger = logging.getLogger(__name__)

# The commerce batch-read endpoint (routers/sellers.py). Kept here as the single source of
# the path so a route change is a one-line edit.
_SHOPS_BY_PROPERTY_PATH = "/api/v1/shops/by-property"
# The single-shop → owning-seller lookup (§8.1b pair-radiate). Same source-of-path convention.
_SHOP_SELLER_PATH = "/api/v1/shops/{shop_id}/seller"


class CommerceReadError(RuntimeError):
    """A commerce S2S read failed (network, timeout, non-OK, or malformed body). The caller
    degrades to no-shop-pins on this — never propagates it as a map error."""


def shops_by_property(user_id: str, role: str, property_uuids: list[str]) -> list[dict]:
    """Ask commerce which of ``property_uuids`` are shops; return the raw shop dicts
    (``property_uuid``, ``shop_id``, ``name``, ``category``).

    ``user_id``/``role`` come from the verified telemetry token — the minted commerce token
    is scoped to that user (least-privilege, auditable), never a service-wide superuser token.
    Empty input short-circuits to ``[]`` with NO network call (the common no-linked-shops AOI).

    Raises ``CommerceReadError`` on any failure so the aggregator can fall back to empty pins.
    """
    if not property_uuids:
        return []
    token = create_commerce_token(user_id, role, scopes=["read:feed"])
    url = f"{settings.commerce_public_url}{_SHOPS_BY_PROPERTY_PATH}"
    try:
        resp = requests.post(
            url,
            json={"property_uuids": property_uuids},
            headers={"Authorization": f"Bearer {token}"},
            timeout=settings.commerce_read_timeout_s,
        )
        resp.raise_for_status()
        body = resp.json()
    except requests.RequestException as e:
        raise CommerceReadError(f"shops_by_property request failed: {e}") from e
    except ValueError as e:  # non-JSON body
        raise CommerceReadError(f"shops_by_property returned non-JSON: {e}") from e
    shops = body.get("shops") if isinstance(body, dict) else None
    if not isinstance(shops, list):
        raise CommerceReadError("shops_by_property response missing a 'shops' list")
    # Defensive: keep only well-formed dict entries (a malformed row can't break the map).
    return [s for s in shops if isinstance(s, dict)]


def seller_uuid_for_shop(user_id: str, role: str, shop_id: str) -> str | None:
    """Resolve a shop to its OWNING seller's weespas user id (§8.1b pair-radiate seam).

    The contact uplink knows the shop a buyer opened and needs the seller's per-user SSE
    channel key to publish the anonymized "a viewer is looking" pulse. Exactly one field crosses
    (``seller_uuid`` == the seller's synchronized weespas identity) — no shop meta, no buyer data,
    no coordinates. ``user_id``/``role`` come from the verified telemetry token; the minted commerce
    token is scoped to that user (least-privilege ``read:feed``), never a service-wide token.

    Returns the seller uuid, or ``None`` when the shop is unknown/unowned (commerce answers 200 with
    ``seller_uuid: null``). Raises ``CommerceReadError`` on any transport failure so the caller can
    degrade to buyer-local glow (skip the publish) rather than error the whole contact."""
    if not shop_id:
        return None
    token = create_commerce_token(user_id, role, scopes=["read:feed"])
    url = f"{settings.commerce_public_url}{_SHOP_SELLER_PATH.format(shop_id=shop_id)}"
    try:
        resp = requests.get(
            url,
            headers={"Authorization": f"Bearer {token}"},
            timeout=settings.commerce_read_timeout_s,
        )
        resp.raise_for_status()
        body = resp.json()
    except requests.RequestException as e:
        raise CommerceReadError(f"seller_uuid_for_shop request failed: {e}") from e
    except ValueError as e:  # non-JSON body
        raise CommerceReadError(f"seller_uuid_for_shop returned non-JSON: {e}") from e
    if not isinstance(body, dict):
        raise CommerceReadError("seller_uuid_for_shop response is not an object")
    seller_uuid = body.get("seller_uuid")
    # null (unknown/unowned shop) is a valid answer; anything non-str-non-null is malformed.
    if seller_uuid is not None and not isinstance(seller_uuid, str):
        raise CommerceReadError("seller_uuid_for_shop response 'seller_uuid' is not a string/null")
    return seller_uuid or None
