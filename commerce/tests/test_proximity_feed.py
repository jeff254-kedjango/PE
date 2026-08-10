"""Proximity feed end-to-end on the SQLite Haversine path.

Asserts: (a) only listings within radius return, (b) ordering matches the pure ranking
(closer+fresher+higher-intent first), (c) cursor pagination yields no dups/gaps.
"""
import json
from datetime import datetime, timedelta, timezone

import pytest

from PE.commerce.models.listing import Listing
from PE.commerce.models.seller import Seller, Shop
from PE.commerce.services import proximity

# Nairobi CBD-ish reference point.
_LAT, _LNG = -1.2921, 36.8219


def _seller(db):
    s = Seller(user_uuid="user-1", display_name="Mama Mboga")
    db.add(s)
    db.flush()
    return s


def _shop(db, seller, lat, lng):
    sh = Shop(seller_id=seller.id, name="Corner Shop")
    proximity.set_location(sh, lat, lng)
    db.add(sh)
    db.flush()
    return sh


def _listing(db, shop, seller, lat, lng, *, title, age_h=1.0, intent=1.0, stock=10):
    li = Listing(
        shop_id=shop.id,
        seller_id=seller.id,
        title=title,
        price_cents=5000,
        currency="KES",
        media_urls=json.dumps(["/uploads/images/x.webp"]),
        intent_weight=intent,
        is_active=True,
        stock_qty=stock,  # in stock so it is feed-eligible (out-of-stock is hidden)
        created_at=datetime.now(timezone.utc) - timedelta(hours=age_h),
    )
    proximity.set_location(li, lat, lng)
    db.add(li)
    db.flush()
    return li


def _km_offset_lat(km):
    return km / 111.32  # ~deg latitude per km


def test_radius_filters_out_far_listings(db_session):
    seller = _seller(db_session)
    near_shop = _shop(db_session, seller, _LAT, _LNG)
    far_shop = _shop(db_session, seller, _LAT + _km_offset_lat(50), _LNG)
    _listing(db_session, near_shop, seller, _LAT, _LNG, title="near")
    _listing(db_session, far_shop, seller, _LAT + _km_offset_lat(50), _LNG, title="far")
    db_session.commit()

    found = proximity.search_listings(db_session, _LAT, _LNG, 2000.0, limit=100)
    titles = {li.title for li, _ in found}
    assert titles == {"near"}


def test_distance_is_measured(db_session):
    seller = _seller(db_session)
    shop = _shop(db_session, seller, _LAT, _LNG)
    # ~1 km north.
    _listing(db_session, shop, seller, _LAT + _km_offset_lat(1), _LNG, title="1km")
    db_session.commit()

    found = proximity.search_listings(db_session, _LAT, _LNG, 5000.0, limit=10)
    assert len(found) == 1
    _, dist = found[0]
    assert 900 < dist < 1100  # ~1000 m within tolerance


def test_feed_orders_closer_first(client, db_session):
    seller = _seller(db_session)
    near = _shop(db_session, seller, _LAT, _LNG)
    mid = _shop(db_session, seller, _LAT + _km_offset_lat(0.5), _LNG)
    _listing(db_session, near, seller, _LAT, _LNG, title="closest")
    _listing(db_session, mid, seller, _LAT + _km_offset_lat(0.5), _LNG, title="further")
    db_session.commit()

    resp = client.get(f"/api/v1/feed?lat={_LAT}&lng={_LNG}&radius_m=2000")
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert [i["title"] for i in items] == ["closest", "further"]
    assert items[0]["score"] >= items[1]["score"]
    # property_uuid surfaced for client-side InSAR stitch (None here, key present).
    assert "property_uuid" in items[0]
    assert items[0]["media_urls"] == ["/uploads/images/x.webp"]


def test_cursor_pagination_no_dupes(client, db_session):
    seller = _seller(db_session)
    shop = _shop(db_session, seller, _LAT, _LNG)
    # 5 listings at increasing distance → deterministic score order.
    for i in range(5):
        _listing(
            db_session, shop, seller,
            _LAT + _km_offset_lat(0.1 * (i + 1)), _LNG,
            title=f"L{i}",
        )
    db_session.commit()

    seen = []
    cursor = None
    for _ in range(5):  # at most 5 pages of size 2
        url = f"/api/v1/feed?lat={_LAT}&lng={_LNG}&radius_m=2000&limit=2"
        if cursor:
            url += f"&cursor={cursor}"
        body = client.get(url).json()
        seen.extend(i["id"] for i in body["items"])
        cursor = body["next_cursor"]
        if not cursor:
            break

    assert len(seen) == 5
    assert len(set(seen)) == 5  # no duplicates, no gaps


def test_candidate_cap_saturation_logs_a_warning(db_session, monkeypatch, caplog):
    """The scaling tripwire: when the radius candidate pull hits feed_max_candidates, the far tail is
    silently dropped before ranking — build_feed must log a WARNING so the ceiling is an ops signal,
    not an invisible correctness gap. Shrink the cap to 2 (instead of seeding 500 rows) and seed 3
    in-radius listings so the pull saturates."""
    import logging

    from PE.commerce.core.config import settings
    from PE.commerce.services import feed as feed_service

    monkeypatch.setattr(settings, "feed_max_candidates", 2)
    seller = _seller(db_session)
    shop = _shop(db_session, seller, _LAT, _LNG)
    for i in range(3):  # 3 in-radius listings, cap is 2 → pull saturates
        _listing(db_session, shop, seller, _LAT + _km_offset_lat(0.05 * (i + 1)), _LNG, title=f"p{i}")
    db_session.commit()

    with caplog.at_level(logging.WARNING, logger="PE.commerce.services.feed"):
        result = feed_service.build_feed(db_session, _LAT, _LNG, 2000.0, limit=20)

    assert len(result["items"]) == 2  # only the capped window was scored
    assert any("saturated the cap" in r.message for r in caplog.records)


def test_candidate_cap_not_saturated_is_silent(db_session, monkeypatch, caplog):
    """The tripwire must NOT cry wolf: a pull comfortably under the cap logs nothing."""
    import logging

    from PE.commerce.core.config import settings
    from PE.commerce.services import feed as feed_service

    monkeypatch.setattr(settings, "feed_max_candidates", 10)
    seller = _seller(db_session)
    shop = _shop(db_session, seller, _LAT, _LNG)
    _listing(db_session, shop, seller, _LAT, _LNG, title="only")
    db_session.commit()

    with caplog.at_level(logging.WARNING, logger="PE.commerce.services.feed"):
        feed_service.build_feed(db_session, _LAT, _LNG, 2000.0, limit=20)

    assert not any("saturated the cap" in r.message for r in caplog.records)


def test_widen_surfaces_nearest_when_immediate_radius_empty(client, db_session):
    """Auto-widen: nothing within the 2 km default, but a shop ~5 km out. The feed must fall back
    once to the server max radius, return that nearest content, flag ``widened`` and report the
    honest ``nearest_distance_m`` — never a dead-end empty surface. ``immediate_count`` is 0 (the
    empty branch), which the client keys the "nothing in your area" copy on."""
    seller = _seller(db_session)
    far = _shop(db_session, seller, _LAT + _km_offset_lat(5), _LNG)
    _listing(db_session, far, seller, _LAT + _km_offset_lat(5), _LNG, title="five-km")
    db_session.commit()

    resp = client.get(f"/api/v1/feed?lat={_LAT}&lng={_LNG}&radius_m=2000")
    assert resp.status_code == 200
    body = resp.json()
    assert [i["title"] for i in body["items"]] == ["five-km"]
    assert body["widened"] is True
    assert body["immediate_count"] == 0  # empty branch
    assert 4800 < body["nearest_distance_m"] < 5200  # ~5 km within tolerance


def test_widen_when_immediate_radius_is_sparse(client, db_session):
    """SPARSE top-up: one local listing (< feed_sparse_threshold) plus a shop ~5 km out. The feed
    tops the thin page up with the far content and flags ``widened`` — but ``immediate_count`` is >0
    so the client says "only a few nearby", NOT "nothing in your area". The NEAR listing must still
    rank FIRST (sparse scores at the original radius, so the far top-up sinks below it)."""
    seller = _seller(db_session)
    near = _shop(db_session, seller, _LAT, _LNG)
    far = _shop(db_session, seller, _LAT + _km_offset_lat(5), _LNG)
    _listing(db_session, near, seller, _LAT, _LNG, title="near")
    _listing(db_session, far, seller, _LAT + _km_offset_lat(5), _LNG, title="far")
    db_session.commit()

    body = client.get(f"/api/v1/feed?lat={_LAT}&lng={_LNG}&radius_m=2000").json()
    assert [i["title"] for i in body["items"]] == ["near", "far"]  # near stays on top
    assert body["widened"] is True
    assert body["immediate_count"] == 1  # sparse branch — had local content
    assert 0 <= body["nearest_distance_m"] < 100  # nearest is the on-point near listing


def test_no_widen_when_local_content_fills_a_page(client, db_session, monkeypatch):
    """Widen must NOT fire when the immediate radius already meets the sparse threshold. Set the
    threshold to 2 and seed 2 near listings (+ a far one that must NOT appear) — the feed stays
    local, ``widened`` false, ``immediate_count`` at/above the threshold."""
    from PE.commerce.core.config import settings
    monkeypatch.setattr(settings, "feed_sparse_threshold", 2)
    seller = _seller(db_session)
    near = _shop(db_session, seller, _LAT, _LNG)
    far = _shop(db_session, seller, _LAT + _km_offset_lat(5), _LNG)
    _listing(db_session, near, seller, _LAT, _LNG, title="near-a")
    _listing(db_session, near, seller, _LAT + _km_offset_lat(0.1), _LNG, title="near-b")
    _listing(db_session, far, seller, _LAT + _km_offset_lat(5), _LNG, title="far")
    db_session.commit()

    body = client.get(f"/api/v1/feed?lat={_LAT}&lng={_LNG}&radius_m=2000").json()
    assert {i["title"] for i in body["items"]} == {"near-a", "near-b"}  # far excluded
    assert body["widened"] is False
    assert body["immediate_count"] == 2


def test_sparse_widen_is_cursor_stable_across_pages(client, db_session, monkeypatch):
    """The widen decision must be identical on every page (a pure function of lat/lng/radius/db), so
    keyset pagination over a sparse+widened feed yields no dups/gaps. Threshold 3; 2 near + 3 far ⇒
    every page widens to the same 5-item set."""
    from PE.commerce.core.config import settings
    monkeypatch.setattr(settings, "feed_sparse_threshold", 3)
    seller = _seller(db_session)
    near = _shop(db_session, seller, _LAT, _LNG)
    far = _shop(db_session, seller, _LAT + _km_offset_lat(5), _LNG)
    _listing(db_session, near, seller, _LAT, _LNG, title="n0")
    _listing(db_session, near, seller, _LAT + _km_offset_lat(0.1), _LNG, title="n1")
    for i in range(3):
        _listing(db_session, far, seller, _LAT + _km_offset_lat(5 + i), _LNG, title=f"f{i}")
    db_session.commit()

    seen, cursor = [], None
    for _ in range(6):
        url = f"/api/v1/feed?lat={_LAT}&lng={_LNG}&radius_m=2000&limit=2"
        if cursor:
            url += f"&cursor={cursor}"
        body = client.get(url).json()
        seen.extend(i["id"] for i in body["items"])
        cursor = body["next_cursor"]
        if not cursor:
            break
    assert len(seen) == 5
    assert len(set(seen)) == 5  # no dups, no gaps across the widened set


def test_widen_still_empty_stays_honest(client, db_session):
    """If even the widened (max-radius) search finds nothing — truly no content — the feed stays
    honest: empty items, ``widened`` false, ``nearest_distance_m`` null, ``immediate_count`` 0. No
    fabricated distance."""
    body = client.get(f"/api/v1/feed?lat={_LAT}&lng={_LNG}&radius_m=2000").json()
    assert body["items"] == []
    assert body["widened"] is False
    assert body["nearest_distance_m"] is None
    assert body["immediate_count"] == 0
