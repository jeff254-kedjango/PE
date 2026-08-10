"""Shop profile + follow ("Notify") service + handle (URL slug) claim — the §8 hovercard's read/
write path plus the §8 storefront's shareable-link primitive.

Three concerns, each keyed by a PUBLIC shop id (anyone may view any shop's profile / follow it):

  * **Profile read** — the shop's published business card + its follower count + this viewer's
    follow state + the owning seller's rating, assembled with O(1) indexed aggregates (never an
    N+1 walk).
  * **Follow toggle** — a (user, shop) subscription mirroring services.engagement.toggle_save
    EXACTLY: idempotent via the UNIQUE constraint, a concurrent follow-race caught as "already
    following" (no 500). It persists the subscription only — delivering a followed shop's stock
    changes is a downstream seam (no notification store yet); the row is the durable intent.
  * **Handle claim** — the shop's shareable URL slug (§8 storefront: /shop/<handle>). ONE-SHOT
    policy: once set, permanent (a rename would break every previously-shared link). Idempotent
    on the same value (POST-again with the current handle returns the shop unchanged); a claim
    against a shop that already has a DIFFERENT handle is a 409; a syntax-invalid or reserved
    handle is 422 at the API edge; a collision against another shop is 409 (case-insensitive).

A shop id is public (it rides in the feed payload), so there is no ownership gate on the read
paths — but a missing shop returns None so the router answers 404 (no fabricated card)."""
from __future__ import annotations

import re

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from PE.commerce.models.seller import Seller, Shop, ShopSubscription


# ----------------------------- handle validation (pure) -----------------------------
#
# A handle is a public, shareable URL slug: /shop/<handle>. The DB enforces case-insensitive
# uniqueness (functional index on lower(handle)); this validator ensures the wire value fits a
# small kebab-case grammar BEFORE the DB round-trip so a garbage claim is a fast 422, not a
# database error. Case-folded to lowercase at the boundary — the DB never sees mixed case.
_HANDLE_MIN = 3
_HANDLE_MAX = 30
# Anchored, allow-listed grammar: lower alnum with SINGLE internal hyphens (no leading/trailing,
# no doubles). ^[a-z0-9]  starts alnum;  ([a-z0-9-]*[a-z0-9])? optional middle+end alnum with
# possible single hyphens; the (?<!-)(?<!--) is unnecessary because the middle class never
# permits "--" — a double hyphen would need a hyphen followed by a hyphen, but our pattern
# consumes hyphens greedily inside `[a-z0-9-]*` and DOES allow `--` in that class. So we split
# the middle into an explicit "alnum-or-single-hyphen" via a non-capturing group.
_HANDLE_RE = re.compile(r"^[a-z0-9](?:[a-z0-9]|-(?=[a-z0-9]))*[a-z0-9]$|^[a-z0-9]$")

# Reserved paths a handle must not shadow. These are the front-of-URL segments the storefront
# routing (or a future admin surface) uses; a handle equal to one of them would break URL
# disambiguation ("was /shop/mine my own storefront or a shop named 'mine'?"). Kept small and
# EXPLICIT — do NOT sneak in policy words here without adding a test.
_RESERVED_HANDLES = frozenset({
    "mine", "admin", "api", "new", "shop", "shops", "sellers", "seller",
    "storefront", "me", "settings", "login", "signup", "about", "help",
    # future-proof: the @-prefixed by-handle route uses "@handle", so a plain "at" isn't
    # reserved, but the empty and single-char cases are ruled out by _HANDLE_MIN.
})


class HandleError(Exception):
    """Handle validation / claim failed. ``status_code`` maps to the HTTP response (422 for
    grammar/reserved-word failures, 409 for collision / already-locked). ``detail`` is safe to
    surface as the API error body (never leaks another seller's data)."""

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def normalize_and_validate_handle(raw: str | None) -> str:
    """Return the lowercased, trimmed handle if it passes every rule; raise HandleError otherwise.

    Rules (in check order, so the error message names the FIRST failure):
      1. Not None / not blank (422 required).
      2. Length in [_HANDLE_MIN, _HANDLE_MAX] AFTER trimming (422 length).
      3. Matches _HANDLE_RE: lower alnum, single internal hyphens, no leading/trailing/double
         hyphen, no uppercase (422 syntax). Callers can pass mixed case and we lower it here.
      4. Not in _RESERVED_HANDLES (422 reserved).

    Never touches the database. Never raises anything except HandleError."""
    if raw is None:
        raise HandleError(422, "handle-required")
    trimmed = raw.strip().lower()
    if not trimmed:
        raise HandleError(422, "handle-required")
    # Reserved check runs BEFORE the length check so a user typing a short reserved word ("me",
    # "api") gets a specific "that name is taken by the platform" message rather than a generic
    # length error — better UX, and it protects the invariant that any reserved word (regardless
    # of length) is unclaimable.
    if trimmed in _RESERVED_HANDLES:
        raise HandleError(422, "handle-reserved")
    if len(trimmed) < _HANDLE_MIN or len(trimmed) > _HANDLE_MAX:
        raise HandleError(422, "handle-length")
    if not _HANDLE_RE.match(trimmed):
        raise HandleError(422, "handle-syntax")
    return trimmed


def _shop(db: Session, shop_id: str) -> Shop | None:
    """The shop by its (public) id, or None (router → 404). One indexed PK lookup."""
    return db.query(Shop).filter(Shop.id == shop_id).one_or_none()


def follower_count(db: Session, shop_id: str) -> int:
    """How many users follow one shop — a single COUNT."""
    return db.query(func.count(ShopSubscription.id)).filter(
        ShopSubscription.shop_id == shop_id
    ).scalar() or 0


def is_following(db: Session, user_uuid: str, shop_id: str) -> bool:
    """Whether the caller follows one shop — one indexed membership lookup."""
    return db.query(
        db.query(ShopSubscription)
        .filter(
            ShopSubscription.user_uuid == user_uuid,
            ShopSubscription.shop_id == shop_id,
        )
        .exists()
    ).scalar() or False


def get_shop(db: Session, shop_id: str) -> Shop | None:
    """The shop for the profile card, or None if it doesn't exist (router → 404)."""
    return _shop(db, shop_id)


def seller_uuid_for_shop(db: Session, shop_id: str) -> str | None:
    """The weespas user id that OWNS a shop (``Seller.user_uuid``), or None if the shop
    doesn't exist. One indexed join (Shop PK → Seller PK) — O(1).

    This is the §8.1b pair-radiate seam: the weespas contact uplink knows the shop the buyer
    opened (``shop_id``) and needs the seller's per-user channel key to publish the anonymized
    "a viewer is looking" pulse. ``user_uuid`` is the ONLY field returned — it is the seller's
    already-synchronized weespas identity (the token ``sub``), not new PII (commerce owns no
    identity, S6). A shop with no owning seller row (impossible under the FK, but defensive)
    yields None."""
    row = (
        db.query(Seller.user_uuid)
        .join(Shop, Shop.seller_id == Seller.id)
        .filter(Shop.id == shop_id)
        .one_or_none()
    )
    return row[0] if row else None


def shops_by_property(db: Session, property_uuids: list[str]) -> list[Shop]:
    """The shops sitting on a batch of building footprints (§8.1a — shops on the InSAR map).

    ONE indexed query (``Shop.property_uuid`` is ``index=True``): an ``IN`` over a bounded batch,
    so this is O(k) on the (already de-duplicated) input, never a table scan (S8). The caller is
    responsible for capping the batch (schemas.SHOPS_BY_PROPERTY_BATCH_MAX) — an empty input
    short-circuits to no query.

    Returns the full Shop rows (the router projects only the non-PII display fields it exposes).
    property_uuid is NOT unique, so two shops on one footprint both come back — the caller must
    not collapse the list by uuid."""
    if not property_uuids:
        return []
    # De-dup the input so a caller repeating a uuid can't inflate the IN or the result; the DB
    # index makes membership O(log n) per key regardless, but a tight IN is cheaper to plan.
    unique = list(dict.fromkeys(property_uuids))
    return (
        db.query(Shop)
        .filter(Shop.property_uuid.in_(unique))
        .all()
    )


# ----------------------------- handle claim + resolve -----------------------------

def _owned_shop(db: Session, shop_id: str, user_uuid: str) -> Shop | None:
    """The shop, only if owned by ``user_uuid``. One indexed join. None ⇒ router 404 with the
    uniform 'shop or listing not found' message (no cross-owner existence leak, S6)."""
    return (
        db.query(Shop)
        .join(Seller, Shop.seller_id == Seller.id)
        .filter(Shop.id == shop_id, Seller.user_uuid == user_uuid)
        .one_or_none()
    )


def get_shop_by_handle(db: Session, handle: str) -> Shop | None:
    """Resolve a handle → Shop (case-insensitive), or None if none exists. One indexed lookup
    against the functional UNIQUE(lower(handle)) index on Postgres; the SQLite test path uses
    the same lower() comparison so the query plan is identical in both environments.

    Callers MUST pass a validator-normalized (lowercased, trimmed) handle. This function does
    NOT re-validate — it's a hot read path and the router already validated the input."""
    return (
        db.query(Shop)
        .filter(func.lower(Shop.handle) == handle)
        .one_or_none()
    )


def is_handle_available(db: Session, handle: str) -> bool:
    """Cheap membership check for the frontend's live-availability probe. One indexed lookup
    (same as get_shop_by_handle but SELECT 1). Assumes the caller has already syntax-validated
    the handle; a syntax-invalid handle should be rejected at the router BEFORE this call."""
    return not db.query(
        db.query(Shop).filter(func.lower(Shop.handle) == handle).exists()
    ).scalar()


def claim_handle(
    db: Session, user_uuid: str, shop_id: str, raw_handle: str | None,
) -> Shop | None:
    """One-shot handle claim (§8 storefront: /shop/<handle>).

    Semantics (each carefully chosen so a rename never breaks a shared link):
      * **Ownership** — the caller must own the shop; a cross-owner call returns None (router →
        uniform 404, no existence leak S6). Ownership is verified BEFORE any validation so an
        attacker probing for handle grammar on other sellers' shops sees the same 404 they'd get
        for a non-existent shop.
      * **Idempotent on the same value** — POSTing the current handle again is a no-op that
        returns the shop unchanged; useful for the frontend's "re-submit" retry after a network
        blip.
      * **One-shot lock** — if the shop already has a DIFFERENT handle set, the claim fails with
        409 handle-locked. This is the policy: a handle, once chosen, is permanent (a rename
        would break every previously-shared /shop/<handle> link).
      * **Case-insensitive collision** — a claim colliding with ANOTHER shop's handle fails with
        409 handle-taken. The functional UNIQUE(lower(handle)) index is the hard backstop; we
        also pre-check via is_handle_available to give a friendlier error in the common case.
        Race: if two concurrent claims win the pre-check simultaneously, the IntegrityError on
        commit is caught and mapped to the same 409 (no 500).
      * **Validation** — the handle is normalized + validated at the boundary (HandleError
        propagates as a 422). Blank/None is 422 handle-required (never a "" handle in the DB).

    Returns the refreshed Shop on success. Raises HandleError with .status_code set for every
    named failure so the router maps 1:1 without further branching."""
    # Ownership FIRST: an unauthorized caller learns nothing about the shop's handle state.
    shop = _owned_shop(db, shop_id, user_uuid)
    if shop is None:
        return None

    handle = normalize_and_validate_handle(raw_handle)  # raises HandleError on invalid

    # Idempotent no-op: the shop already claimed this exact handle (case-insensitive).
    if shop.handle is not None and shop.handle.lower() == handle:
        return shop

    # One-shot lock: a different handle is already set — refuse the change (rename → dead links).
    if shop.handle is not None:
        raise HandleError(409, "handle-locked")

    # Cheap pre-check to surface a friendlier 409 (the IntegrityError on commit is the backstop
    # for the concurrent-claim race).
    if not is_handle_available(db, handle):
        raise HandleError(409, "handle-taken")

    shop.handle = handle
    try:
        db.commit()
    except IntegrityError:
        # Concurrent claim won the race — a second caller inserted the same lower(handle) between
        # our is_handle_available check and our commit. Roll back and surface the same 409 the
        # pre-check would have raised (no 500 exposed to the client).
        db.rollback()
        raise HandleError(409, "handle-taken")
    db.refresh(shop)
    return shop


def toggle_follow(db: Session, user_uuid: str, shop_id: str) -> tuple[bool, int] | None:
    """Toggle the caller's follow on a shop. Returns ``(following, follower_count)`` or None if the
    shop doesn't exist (router → 404).

    Idempotent under a follow→follow race: the UNIQUE (user_uuid, shop_id) turns a concurrent
    double-insert into an IntegrityError, treated as 'already following' rather than a 500 —
    mirrors services.engagement.toggle_save exactly."""
    if _shop(db, shop_id) is None:
        return None
    existing = (
        db.query(ShopSubscription)
        .filter(
            ShopSubscription.user_uuid == user_uuid,
            ShopSubscription.shop_id == shop_id,
        )
        .one_or_none()
    )
    if existing is not None:
        db.delete(existing)
        db.commit()
        return False, follower_count(db, shop_id)
    db.add(ShopSubscription(user_uuid=user_uuid, shop_id=shop_id))
    try:
        db.commit()
    except IntegrityError:
        # Concurrent follow won the race — already exists. Treat as following (idempotent).
        db.rollback()
    return True, follower_count(db, shop_id)
