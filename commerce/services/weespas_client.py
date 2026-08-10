"""Commerce → weespas S2S client (§8 Chunk C+).

One purpose: call weespas `POST /commerce/users/lookup` to hydrate viewer identity for the
seller-console Viewing Card. Everything else in this module exists to serve that ONE call
gracefully.

Design contract:
  * Sync (not async). The only caller (the /shops/{id}/live-viewers endpoint) is sync;
    plumbing async into a sync router just to avoid a blocking HTTP call is more risk
    (event-loop leaks) than value.
  * Fail-open on identity. A weespas outage, timeout, or misconfigured secret NEVER makes
    the seller's card 500. The client returns an empty map ({}); the caller falls back to
    labelling every viewer 'Guest'. The seller sees a diminished card, not a broken one.
  * Fail-closed on the SECRET. If the secret isn't set in commerce config, we don't make
    the call at all — no half-authenticated attempt.
  * Bounded per-call. 100 uuids per lookup (matches the weespas cap). Callers with more
    should chunk; the /live-viewers endpoint tops out at ~30 live viewers in practice, so
    a single call always suffices.
  * Bounded time. Hard timeout from settings (default 2s). A slow weespas is treated the
    same as an unavailable one — the seller waits at most ~2 seconds for the card body.

The alternative — a shared secret in a header we didn't own — would require rotating both
services in lockstep. This module is the ONLY place commerce ever calls back to weespas;
if that changes we'll want to refactor into a `services/weespas_client/` package with the
shared http concerns extracted.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional

import httpx

from PE.commerce.core.config import settings


# Cap on how many uuids we send in one call — matches the weespas /commerce/users/lookup
# cap. A caller with more viewers should chunk, but the endpoint's live-viewer set is
# small (< 30 typical), so this is a defensive backstop.
_MAX_BATCH = 100


@dataclass(frozen=True)
class UserSummary:
    """One viewer's hydrated identity. `phone` is included when weespas has it AND policy
    allows the seller to see it — the followers-only rule is applied by the caller (see
    services/live_viewers.py), NOT here. The client is stateless."""
    uuid: str
    display_name: str
    avatar_url: Optional[str] = None
    phone: Optional[str] = None


def lookup_user_summaries(uuids: Iterable[str]) -> dict[str, UserSummary]:
    """Look up display_name + avatar + phone for the given uuids on weespas.

    Return shape: dict keyed by uuid. Callers deref by id — missing uuids are simply
    absent (they'll be labelled 'Guest' by the caller). ORDER not preserved — dict.

    Graceful-degradation matrix (all return an empty dict rather than raising):
      * secret unset in config           → no call, empty dict.
      * empty input                      → no call, empty dict.
      * http timeout / network error     → empty dict.
      * non-2xx response                 → empty dict.
      * malformed JSON body              → empty dict.

    None of these paths ever surface to the seller. The caller decides what an empty dict
    means (typically: label every viewer 'Guest' — the card still renders, just without
    faces/names). This is deliberate: a bridge outage must NEVER make the card unresponsive.

    Uuid batch is capped at _MAX_BATCH; anything larger is silently truncated. In practice
    the live-viewers set is < 30, so this is a defensive backstop.
    """
    # Fail-closed on the secret — no half-authenticated attempts.
    secret = settings.weespas_users_lookup_secret
    if not secret:
        return {}

    # Dedup + cap. Input order is not preserved — the response is a dict keyed by uuid.
    unique = list(dict.fromkeys(uuids))
    if not unique:
        return {}
    if len(unique) > _MAX_BATCH:
        unique = unique[:_MAX_BATCH]

    url = f"{settings.weespas_url.rstrip('/')}/api/v1/commerce/users/lookup"
    try:
        r = httpx.post(
            url,
            json={"uuids": unique},
            headers={"X-Service-Secret": secret, "Content-Type": "application/json"},
            timeout=settings.weespas_lookup_timeout_s,
        )
    except httpx.HTTPError:
        # Timeout, connection refused, DNS failure, TLS error — all collapse to "no summary
        # available". Never propagate up.
        return {}

    if r.status_code != 200:
        # 401 (wrong secret), 503 (weespas bridge disabled), 5xx (weespas outage), 422
        # (validation surprise) — all treated the same. Log-worthy in prod but never raised.
        return {}

    try:
        body = r.json()
        items = body.get("items", [])
    except (ValueError, AttributeError):
        return {}

    out: dict[str, UserSummary] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        uuid = item.get("uuid")
        if not uuid:
            continue
        out[uuid] = UserSummary(
            uuid=uuid,
            display_name=item.get("display_name") or "",
            avatar_url=item.get("avatar_url"),
            phone=item.get("phone"),
        )
    return out
