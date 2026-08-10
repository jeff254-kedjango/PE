"""Social engagement — saves (idempotent toggle + display-only social proof) and inquiries.

Real RS256 tokens so the audience-scope gate and per-user identity (token sub) run end to end.
Saving/asking need only the commerce_trade audience (read:feed token), NOT create:trades —
buyers engage. A seller token is used only to seed shops/listings.
"""
from datetime import datetime, timedelta, timezone
from pathlib import Path

import jwt
import pytest
from fastapi.testclient import TestClient

from PE.commerce.core.database import get_db
from PE.commerce.main import app

_KEYS = Path(__file__).resolve().parents[3] / "PE" / "dev" / "keys"
_PRIVATE = (_KEYS / "insar_jwt_private.pem").read_text()

_SELLER_SCOPES = ("read:feed", "create:trades")
_BUYER_SCOPES = ("read:feed",)
_LAT, _LNG = -1.2920, 36.8219


def _mint(sub, scopes, name=None, role="user"):
    payload = {
        "sub": sub,
        "role": role,
        "scope": "commerce_trade",
        "scopes": list(scopes),
        "exp": datetime.now(timezone.utc) + timedelta(minutes=10),
    }
    if name is not None:
        payload["name"] = name
    return jwt.encode(payload, _PRIVATE, algorithm="RS256")


def _auth(sub, scopes=_BUYER_SCOPES, name=None, role="user"):
    return {"Authorization": f"Bearer {_mint(sub, scopes, name, role)}"}


@pytest.fixture
def client(db_session):
    app.dependency_overrides.clear()

    def _override_db():
        yield db_session

    app.dependency_overrides[get_db] = _override_db
    yield TestClient(app, raise_server_exceptions=False)
    app.dependency_overrides.clear()


def _seed_listing(client, sub="seller-A", stock=5):
    shop = client.post(
        "/api/v1/shops",
        json={"name": "Shop", "lat": _LAT, "lng": _LNG, "display_name": "A"},
        headers=_auth(sub, _SELLER_SCOPES),
    ).json()
    li = client.post(
        f"/api/v1/shops/{shop['id']}/listings",
        json={"title": "Tomatoes", "price_cents": 5000, "stock_qty": stock},
        headers=_auth(sub, _SELLER_SCOPES),
    ).json()
    return li["id"]


# --------------------------- saves ---------------------------

def test_save_toggle_and_idempotency(client):
    lid = _seed_listing(client)
    buyer = _auth("buyer-1")

    r = client.post(f"/api/v1/listings/{lid}/save", headers=buyer).json()
    assert r == {"listing_id": lid, "saved": True, "save_count": 1}

    # saving again is idempotent — still saved, still 1, no 500
    r = client.post(f"/api/v1/listings/{lid}/save", headers=buyer)
    assert r.status_code == 200
    # second call toggles OFF (a POST toggle): saved False, count 0
    assert r.json() == {"listing_id": lid, "saved": False, "save_count": 0}


def test_two_users_independent_saves(client):
    lid = _seed_listing(client)
    client.post(f"/api/v1/listings/{lid}/save", headers=_auth("buyer-1"))
    r = client.post(f"/api/v1/listings/{lid}/save", headers=_auth("buyer-2")).json()
    assert r["save_count"] == 2 and r["saved"] is True


def test_save_nonexistent_listing_404(client):
    assert client.post(
        "/api/v1/listings/does-not-exist/save", headers=_auth("buyer-1")
    ).status_code == 404


def test_my_saves_lists_and_unsave_removes(client):
    lid = _seed_listing(client)
    buyer = _auth("buyer-1")
    client.post(f"/api/v1/listings/{lid}/save", headers=buyer)

    saves = client.get("/api/v1/me/saves", headers=buyer).json()
    assert len(saves["items"]) == 1
    assert saves["items"][0]["listing"]["id"] == lid
    assert saves["items"][0]["listing"]["title"] == "Tomatoes"

    # another user sees none
    assert client.get("/api/v1/me/saves", headers=_auth("buyer-2")).json()["items"] == []

    # unsave → gone from my saves
    client.post(f"/api/v1/listings/{lid}/save", headers=buyer)
    assert client.get("/api/v1/me/saves", headers=buyer).json()["items"] == []


# --------------------------- feed social proof (display only) ---------------------------

def test_feed_shows_save_count_without_changing_order(client):
    a = _seed_listing(client, sub="seller-A")
    b = _seed_listing(client, sub="seller-B")
    buyer = _auth("buyer-1")

    def order_and_counts():
        items = client.get(f"/api/v1/feed?lat={_LAT}&lng={_LNG}", headers=buyer).json()["items"]
        return [i["id"] for i in items], {i["id"]: i["save_count"] for i in items}

    order_before, counts_before = order_and_counts()
    assert counts_before == {a: 0, b: 0}

    # save one listing twice (two users) → save_count 2, but order must NOT change
    client.post(f"/api/v1/listings/{a}/save", headers=_auth("buyer-1"))
    client.post(f"/api/v1/listings/{a}/save", headers=_auth("buyer-2"))

    order_after, counts_after = order_and_counts()
    assert counts_after[a] == 2 and counts_after[b] == 0
    assert order_after == order_before  # display-only: ranking unaffected by saves


def test_feed_saved_by_me_is_per_viewer(client):
    # The feed marks which listings THIS caller has saved (so the card's heart reflects prior saves
    # on a fresh mount) — and it is scoped to the viewer, never another user's saves.
    a = _seed_listing(client, sub="seller-A")
    b = _seed_listing(client, sub="seller-B")

    def saved_map(headers):
        items = client.get(f"/api/v1/feed?lat={_LAT}&lng={_LNG}", headers=headers).json()["items"]
        return {i["id"]: i["saved_by_me"] for i in items}

    # Before saving anything, nothing is saved for either viewer.
    assert saved_map(_auth("buyer-1")) == {a: False, b: False}

    client.post(f"/api/v1/listings/{a}/save", headers=_auth("buyer-1"))

    # buyer-1 sees only their own save; buyer-2 (who saved nothing) sees none — no cross-viewer leak.
    assert saved_map(_auth("buyer-1")) == {a: True, b: False}
    assert saved_map(_auth("buyer-2")) == {a: False, b: False}

    # Un-saving flips it back (idempotent toggle) — the flag tracks current state, not history.
    client.post(f"/api/v1/listings/{a}/save", headers=_auth("buyer-1"))
    assert saved_map(_auth("buyer-1")) == {a: False, b: False}


# --------------------------- inquiries ---------------------------

def test_inquiry_reaches_seller_inbox_only(client):
    lid = _seed_listing(client, sub="seller-A")
    # buyer asks (default message)
    r = client.post(f"/api/v1/listings/{lid}/inquiries", json={}, headers=_auth("buyer-1"))
    assert r.status_code == 201
    body = r.json()
    assert body["message"] == "Is this still available?"
    assert body["from_user_uuid"] == "buyer-1"
    assert body["listing_title"] == "Tomatoes"

    # the seller sees it
    inbox = client.get("/api/v1/me/inquiries", headers=_auth("seller-A", _SELLER_SCOPES)).json()
    assert len(inbox["items"]) == 1
    assert inbox["items"][0]["message"] == "Is this still available?"
    assert inbox["items"][0]["is_read"] is False

    # a DIFFERENT seller does not
    other = client.get("/api/v1/me/inquiries", headers=_auth("seller-B", _SELLER_SCOPES)).json()
    assert other["items"] == []


def test_inquiry_custom_message_and_validation(client):
    lid = _seed_listing(client)
    buyer = _auth("buyer-1")
    # custom message
    r = client.post(
        f"/api/v1/listings/{lid}/inquiries", json={"message": "Do you have 5kg?"}, headers=buyer
    )
    assert r.status_code == 201 and r.json()["message"] == "Do you have 5kg?"
    # empty → 422
    assert client.post(
        f"/api/v1/listings/{lid}/inquiries", json={"message": ""}, headers=buyer
    ).status_code == 422
    # too long → 422
    assert client.post(
        f"/api/v1/listings/{lid}/inquiries", json={"message": "x" * 501}, headers=buyer
    ).status_code == 422


def test_inquiry_nonexistent_listing_404(client):
    assert client.post(
        "/api/v1/listings/nope/inquiries", json={}, headers=_auth("buyer-1")
    ).status_code == 404


def test_mark_inquiry_read_recipient_only(client):
    lid = _seed_listing(client, sub="seller-A")
    client.post(f"/api/v1/listings/{lid}/inquiries", json={}, headers=_auth("buyer-1"))
    inq_id = client.get(
        "/api/v1/me/inquiries", headers=_auth("seller-A", _SELLER_SCOPES)
    ).json()["items"][0]["id"]

    # a non-recipient cannot mark it read → 404 (no existence leak)
    assert client.patch(
        f"/api/v1/inquiries/{inq_id}/read", headers=_auth("seller-B", _SELLER_SCOPES)
    ).status_code == 404

    # the recipient can → 204, and it flips to read
    assert client.patch(
        f"/api/v1/inquiries/{inq_id}/read", headers=_auth("seller-A", _SELLER_SCOPES)
    ).status_code == 204
    inbox = client.get("/api/v1/me/inquiries", headers=_auth("seller-A", _SELLER_SCOPES)).json()
    assert inbox["items"][0]["is_read"] is True


# --------------------------- comments (public thread) ---------------------------

def test_comment_post_and_list_newest_first(client):
    lid = _seed_listing(client, sub="seller-A")
    # two different buyers comment publicly
    r1 = client.post(
        f"/api/v1/listings/{lid}/comments", json={"body": "Is the price firm?"},
        headers=_auth("buyer-1"),
    )
    assert r1.status_code == 201
    assert r1.json()["body"] == "Is the price firm?"
    assert r1.json()["author_uuid"] == "buyer-1"
    client.post(
        f"/api/v1/listings/{lid}/comments", json={"body": "I'll take two"},
        headers=_auth("buyer-2"),
    )

    # public thread is visible to anyone authenticated, newest-first
    thread = client.get(f"/api/v1/listings/{lid}/comments", headers=_auth("buyer-9")).json()
    assert [c["body"] for c in thread["items"]] == ["I'll take two", "Is the price firm?"]


# --------------------------- newest-first determinism under a created_at TIE ---------------------------
#
# Regression for the random-uuid tie-break bug: the three engagement threads formerly ordered by
# (created_at DESC, id DESC), and ``id`` is a random uuid4 — so rows sharing a created_at
# microsecond ordered non-deterministically. The fix is a per-scope monotonic ``seq``. These tests
# FREEZE the timestamp to a single constant (via the model's ``utcnow`` default) so EVERY row ties
# on created_at, isolating seq as the sole ordering signal: if ordering were still created_at-based
# it would be arbitrary and these would flake; with seq it is exactly insertion order, reversed.

@pytest.fixture
def frozen_clock():
    """Pin every engagement table's ``created_at`` default to one constant instant, so all rows in
    the test tie on created_at and only ``seq`` can disambiguate the newest-first order.

    NOTE: we cannot monkeypatch ``models.engagement.utcnow`` — SQLAlchemy binds ``default=utcnow``
    at class-definition time, wrapping the ORIGINAL function object in the Column's
    ``CallableColumnDefault`` closure, so rebinding the module name never reaches the insert path.
    Instead we swap each Column's ``.default`` for a constant-returning ``ColumnDefault`` and
    restore it on teardown — the only mechanism that actually forces the created_at tie."""
    from sqlalchemy.sql.schema import ColumnDefault

    from PE.commerce.models.engagement import (
        ListingComment,
        ListingInquiry,
        SavedListing,
    )

    fixed = datetime(2026, 7, 11, 12, 0, 0, tzinfo=timezone.utc)
    cols = [
        m.__table__.c.created_at
        for m in (SavedListing, ListingInquiry, ListingComment)
    ]
    saved = [c.default for c in cols]
    for c in cols:
        c.default = ColumnDefault(lambda: fixed)
    try:
        yield fixed
    finally:
        for c, original in zip(cols, saved):
            c.default = original


def test_comment_thread_newest_first_is_deterministic_under_created_at_tie(client, frozen_clock):
    lid = _seed_listing(client, sub="seller-A")
    bodies = [f"comment-{i}" for i in range(6)]
    for body in bodies:
        assert client.post(
            f"/api/v1/listings/{lid}/comments", json={"body": body}, headers=_auth("buyer-1"),
        ).status_code == 201

    thread = client.get(f"/api/v1/listings/{lid}/comments", headers=_auth("buyer-9")).json()
    # Strict newest-first = reversed insertion order, even though all six share one created_at.
    assert [c["body"] for c in thread["items"]] == list(reversed(bodies))


def test_my_saves_newest_first_is_deterministic_under_created_at_tie(client, frozen_clock):
    buyer = _auth("buyer-1")
    lids = [_seed_listing(client, sub=f"seller-{i}") for i in range(6)]
    for lid in lids:
        assert client.post(f"/api/v1/listings/{lid}/save", headers=buyer).status_code == 200

    saves = client.get("/api/v1/me/saves", headers=buyer).json()
    assert [s["listing"]["id"] for s in saves["items"]] == list(reversed(lids))


def test_seller_inbox_newest_first_is_deterministic_under_created_at_tie(client, frozen_clock):
    lid = _seed_listing(client, sub="seller-A")
    messages = [f"ask-{i}" for i in range(6)]
    for i, msg in enumerate(messages):
        assert client.post(
            f"/api/v1/listings/{lid}/inquiries", json={"message": msg}, headers=_auth(f"buyer-{i}"),
        ).status_code == 201

    inbox = client.get("/api/v1/me/inquiries", headers=_auth("seller-A", _SELLER_SCOPES)).json()
    assert [inq["message"] for inq in inbox["items"]] == list(reversed(messages))


def test_comment_keyset_pagination_no_dupe_or_drop_under_created_at_tie(client, frozen_clock):
    """The seq cursor must page cleanly even when every row ties on created_at — the exact
    condition the old (created_at, id) cursor mishandled (a boundary row could repeat or vanish)."""
    lid = _seed_listing(client, sub="seller-A")
    bodies = [f"page-{i}" for i in range(5)]
    for body in bodies:
        client.post(f"/api/v1/listings/{lid}/comments", json={"body": body}, headers=_auth("buyer-1"))

    reader = _auth("buyer-9")
    seen: list[str] = []
    url = f"/api/v1/listings/{lid}/comments?limit=2"
    while url:
        page = client.get(url, headers=reader).json()
        seen.extend(c["body"] for c in page["items"])
        cursor = page["next_cursor"]
        url = f"/api/v1/listings/{lid}/comments?limit=2&cursor={cursor}" if cursor else None

    # Every comment exactly once, in strict newest-first order — no dropped or repeated boundary row.
    assert seen == list(reversed(bodies))


def test_comment_snapshots_author_name_from_token(client):
    """The commenter's display name (token `name` claim) is snapshotted onto the comment so the
    thread shows a name, not the raw uuid. A claim-less token leaves it null (UI falls back)."""
    lid = _seed_listing(client, sub="seller-A")
    named = client.post(
        f"/api/v1/listings/{lid}/comments", json={"body": "Available?"},
        headers=_auth("buyer-1", name="Asha Kimani"),
    )
    assert named.status_code == 201
    assert named.json()["author_name"] == "Asha Kimani"
    # A token without the name claim → null snapshot (not the uuid).
    nameless = client.post(
        f"/api/v1/listings/{lid}/comments", json={"body": "Price?"}, headers=_auth("buyer-2"),
    )
    assert nameless.status_code == 201
    assert nameless.json()["author_name"] is None
    # The snapshot survives a thread re-read (newest-first).
    thread = client.get(f"/api/v1/listings/{lid}/comments", headers=_auth("buyer-9")).json()
    assert thread["items"][0]["author_name"] is None
    assert thread["items"][1]["author_name"] == "Asha Kimani"


def test_inquiry_snapshots_from_user_name_in_seller_inbox(client):
    """The asker's display name is snapshotted onto the inquiry so the seller inbox shows a name."""
    lid = _seed_listing(client, sub="seller-A")
    posted = client.post(
        f"/api/v1/listings/{lid}/inquiries", json={"message": "Still got it?"},
        headers=_auth("buyer-1", name="Brian Otieno"),
    )
    assert posted.status_code == 201
    assert posted.json()["from_user_name"] == "Brian Otieno"
    inbox = client.get("/api/v1/me/inquiries", headers=_auth("seller-A", _SELLER_SCOPES)).json()
    assert inbox["items"][0]["from_user_name"] == "Brian Otieno"


def test_comment_trims_and_rejects_empty_or_oversized(client):
    lid = _seed_listing(client)
    buyer = _auth("buyer-1")
    # whitespace-only → 422 (schema min_length on the raw body)
    assert client.post(
        f"/api/v1/listings/{lid}/comments", json={"body": "   "}, headers=buyer
    ).status_code == 422
    # empty → 422
    assert client.post(
        f"/api/v1/listings/{lid}/comments", json={"body": ""}, headers=buyer
    ).status_code == 422
    # oversized → 422
    assert client.post(
        f"/api/v1/listings/{lid}/comments", json={"body": "x" * 2001}, headers=buyer
    ).status_code == 422
    # surrounding whitespace is trimmed on a valid body
    r = client.post(
        f"/api/v1/listings/{lid}/comments", json={"body": "  hi  "}, headers=buyer
    )
    assert r.status_code == 201 and r.json()["body"] == "hi"


def test_comment_nonexistent_listing_404(client):
    assert client.post(
        "/api/v1/listings/nope/comments", json={"body": "hi"}, headers=_auth("buyer-1")
    ).status_code == 404
    assert client.get(
        "/api/v1/listings/nope/comments", headers=_auth("buyer-1")
    ).status_code == 404


def test_feed_shows_comment_count_without_changing_order(client):
    a = _seed_listing(client, sub="seller-A")
    b = _seed_listing(client, sub="seller-B")
    buyer = _auth("buyer-1")

    def order_and_counts():
        items = client.get(f"/api/v1/feed?lat={_LAT}&lng={_LNG}", headers=buyer).json()["items"]
        return [i["id"] for i in items], {i["id"]: i["comment_count"] for i in items}

    order_before, counts_before = order_and_counts()
    assert counts_before == {a: 0, b: 0}

    client.post(f"/api/v1/listings/{a}/comments", json={"body": "one"}, headers=_auth("buyer-1"))
    client.post(f"/api/v1/listings/{a}/comments", json={"body": "two"}, headers=_auth("buyer-2"))

    order_after, counts_after = order_and_counts()
    assert counts_after[a] == 2 and counts_after[b] == 0
    assert order_after == order_before  # display-only: ranking unaffected by comments


# --------------------------- §8 comment likes ("love") ---------------------------

def _seed_comment(client, lid, sub="buyer-1", body="Nice one"):
    return client.post(
        f"/api/v1/listings/{lid}/comments", json={"body": body}, headers=_auth(sub),
    ).json()["id"]


def test_comment_like_toggle_and_idempotency(client):
    lid = _seed_listing(client)
    cid = _seed_comment(client, lid)
    buyer = _auth("buyer-2")

    r = client.post(f"/api/v1/comments/{cid}/like", headers=buyer).json()
    assert r == {"comment_id": cid, "liked": True, "like_count": 1}
    # Toggling off removes the like.
    r = client.post(f"/api/v1/comments/{cid}/like", headers=buyer).json()
    assert r == {"comment_id": cid, "liked": False, "like_count": 0}


def test_comment_like_counts_distinct_users(client):
    lid = _seed_listing(client)
    cid = _seed_comment(client, lid)
    client.post(f"/api/v1/comments/{cid}/like", headers=_auth("buyer-1"))
    client.post(f"/api/v1/comments/{cid}/like", headers=_auth("buyer-2"))
    client.post(f"/api/v1/comments/{cid}/like", headers=_auth("buyer-3"))
    thread = client.get(f"/api/v1/listings/{lid}/comments", headers=_auth("buyer-9")).json()
    c = next(c for c in thread["items"] if c["id"] == cid)
    assert c["like_count"] == 3  # the count is distinct likers


def test_comment_like_liked_by_me_is_per_viewer(client):
    lid = _seed_listing(client)
    cid = _seed_comment(client, lid)
    client.post(f"/api/v1/comments/{cid}/like", headers=_auth("buyer-1"))

    def liked_by(sub):
        thread = client.get(f"/api/v1/listings/{lid}/comments", headers=_auth(sub)).json()
        return next(c for c in thread["items"] if c["id"] == cid)["liked_by_me"]

    assert liked_by("buyer-1") is True   # the liker sees it filled
    assert liked_by("buyer-2") is False  # everyone else sees it empty


def test_comment_like_nonexistent_404(client):
    assert client.post(
        "/api/v1/comments/nope/like", headers=_auth("buyer-1")
    ).status_code == 404


# --------------------------- §8 comment moderation (soft-hide) ---------------------------

def _hide(client, cid, headers, hidden=True):
    return client.patch(f"/api/v1/comments/{cid}/hidden", json={"hidden": hidden}, headers=headers)


def _thread_ids(client, lid):
    thread = client.get(f"/api/v1/listings/{lid}/comments", headers=_auth("viewer")).json()
    return [c["id"] for c in thread["items"]]


def test_staff_can_hide_and_unhide_a_comment(client):
    lid = _seed_listing(client)  # owned by seller-A
    cid = _seed_comment(client, lid, sub="buyer-1", body="spam")
    staff = _auth("moderator-1", role="staff")

    assert _hide(client, cid, staff).status_code == 204
    # Hidden: gone from the thread AND the feed comment_count.
    assert cid not in _thread_ids(client, lid)
    item = next(i for i in client.get(f"/api/v1/feed?lat={_LAT}&lng={_LNG}", headers=_auth("v")).json()["items"] if i["id"] == lid)
    assert item["comment_count"] == 0
    # Un-hide restores it.
    assert _hide(client, cid, staff, hidden=False).status_code == 204
    assert cid in _thread_ids(client, lid)


def test_listing_owner_can_hide_a_comment_on_their_thread(client):
    lid = _seed_listing(client, sub="seller-A")
    cid = _seed_comment(client, lid, sub="buyer-1", body="rude")
    # The seller who owns the listing may moderate its thread (create:trades token, role user).
    owner = _auth("seller-A", _SELLER_SCOPES)
    assert _hide(client, cid, owner).status_code == 204
    assert cid not in _thread_ids(client, lid)


def test_non_owner_non_staff_cannot_moderate_404_no_leak(client):
    lid = _seed_listing(client, sub="seller-A")
    cid = _seed_comment(client, lid, sub="buyer-1")
    # A random buyer (not staff, doesn't own the listing) gets 404 — never 403 (no existence leak).
    r = _hide(client, cid, _auth("random-buyer"))
    assert r.status_code == 404
    # And the comment is untouched (still visible).
    assert cid in _thread_ids(client, lid)


def test_moderate_nonexistent_comment_404(client):
    assert _hide(client, "nope", _auth("moderator", role="staff")).status_code == 404


def test_a_different_seller_cannot_moderate_anothers_thread(client):
    lid = _seed_listing(client, sub="seller-A")
    cid = _seed_comment(client, lid, sub="buyer-1")
    # seller-B owns their own shop but NOT this listing → 404.
    _seed_listing(client, sub="seller-B")
    assert _hide(client, cid, _auth("seller-B", _SELLER_SCOPES)).status_code == 404


def test_moderate_requires_token(client):
    assert client.patch("/api/v1/comments/x/hidden", json={"hidden": True}).status_code == 401


# --------------------------- §8 feed kind toggle + short-video flag ---------------------------

def _seed_listing_kind(client, sub, *, is_short_video, stock=5):
    shop = client.post(
        "/api/v1/shops",
        json={"name": "Shop", "lat": _LAT, "lng": _LNG, "display_name": sub},
        headers=_auth(sub, _SELLER_SCOPES),
    ).json()
    li = client.post(
        f"/api/v1/shops/{shop['id']}/listings",
        json={"title": "T", "price_cents": 5000, "stock_qty": stock,
              "is_short_video": is_short_video},
        headers=_auth(sub, _SELLER_SCOPES),
    ).json()
    return li["id"], li


def test_listing_create_carries_is_short_video(client):
    _, plain = _seed_listing_kind(client, "seller-A", is_short_video=False)
    _, video = _seed_listing_kind(client, "seller-B", is_short_video=True)
    assert plain["is_short_video"] is False
    assert video["is_short_video"] is True


def test_feed_kind_toggle_filters_post_type(client):
    plain, _ = _seed_listing_kind(client, "seller-A", is_short_video=False)
    video, _ = _seed_listing_kind(client, "seller-B", is_short_video=True)
    buyer = _auth("buyer-1")

    def ids(kind=None):
        q = f"/api/v1/feed?lat={_LAT}&lng={_LNG}"
        if kind:
            q += f"&kind={kind}"
        items = client.get(q, headers=buyer).json()["items"]
        return {i["id"] for i in items}

    both = ids()
    assert {plain, video} <= both
    assert ids("listings") & {plain, video} == {plain}
    assert ids("videos") & {plain, video} == {video}
    # the flag rides through to the feed item
    items = client.get(f"/api/v1/feed?lat={_LAT}&lng={_LNG}&kind=videos", headers=buyer).json()["items"]
    assert all(i["is_short_video"] for i in items)


def test_feed_invalid_kind_422(client):
    assert client.get(
        f"/api/v1/feed?lat={_LAT}&lng={_LNG}&kind=bogus", headers=_auth("buyer-1")
    ).status_code == 422


# --------------------------- auth ---------------------------

def test_engagement_requires_token(client):
    assert client.post("/api/v1/listings/x/save").status_code == 401
    assert client.get("/api/v1/me/saves").status_code == 401
    assert client.post("/api/v1/listings/x/inquiries", json={}).status_code == 401
    assert client.get("/api/v1/me/inquiries").status_code == 401
    assert client.post("/api/v1/listings/x/comments", json={"body": "hi"}).status_code == 401
    assert client.get("/api/v1/listings/x/comments").status_code == 401
