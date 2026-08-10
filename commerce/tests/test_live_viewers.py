"""Hydrated live-viewers tests (§8 Chunk C+).

Covers both the service (services/live_viewers.get_hydrated_live_viewers) and the endpoint
(GET /shops/{id}/live-viewers).

The service composes weespas_client.lookup_user_summaries + reverse_geocode + a listing
lookup + a followers-only phone filter. Tests use a monkeypatched bridge (never HTTP) so
they run offline. Reverse-geocoding uses the real seeded neighbourhoods table.
"""
from datetime import datetime, timedelta, timezone
from pathlib import Path

import jwt
import pytest
from fastapi.testclient import TestClient

from PE.commerce.core.database import get_db
from PE.commerce.main import app
from PE.commerce.models.listing import Listing
from PE.commerce.models.seller import Seller, ShopSubscription
from PE.commerce.services import live_viewers as live_svc
from PE.commerce.services import reverse_geocode as geo
from PE.commerce.services import shop_views as views_svc
from PE.commerce.services import weespas_client
from PE.commerce.services.proximity import set_location

_KEYS = Path(__file__).resolve().parents[3] / "PE" / "dev" / "keys"
_PRIVATE = (_KEYS / "insar_jwt_private.pem").read_text()
_SELLER = ("read:feed", "create:trades")
_LAT, _LNG = -1.2920, 36.8219   # Nairobi-ish default

# Coord inside the Kilimani rectangle — anchor for reverse-geocode assertions.
_KILIMANI = (-1.2900, 36.7870)
# Coord for CBD (Central Business District).
_CBD = (-1.2860, 36.8220)


def _mint(sub, scopes=_SELLER):
    return jwt.encode(
        {"sub": sub, "role": "user", "scope": "commerce_trade", "scopes": list(scopes),
         "exp": datetime.now(timezone.utc) + timedelta(minutes=10)},
        _PRIVATE, algorithm="RS256",
    )


def _auth(sub, scopes=_SELLER):
    return {"Authorization": f"Bearer {_mint(sub, scopes)}"}


@pytest.fixture
def client(db_session):
    """TestClient with get_db override + neighbourhoods seeded so reverse_geocode returns
    actual area labels (the seed is idempotent — safe to run per-test)."""
    app.dependency_overrides.clear()
    geo.ensure_seeded(db_session)

    def _override_db():
        yield db_session
    app.dependency_overrides[get_db] = _override_db
    yield TestClient(app, raise_server_exceptions=False)
    app.dependency_overrides.clear()


def _open_shop(client, seller_sub, name="Shop"):
    r = client.post(
        "/api/v1/shops",
        json={"name": name, "lat": _LAT, "lng": _LNG, "display_name": seller_sub},
        headers=_auth(seller_sub),
    )
    assert r.status_code == 201, r.text
    return r.json()


def _stub_bridge(monkeypatch, mapping: dict[str, dict]):
    """Replace the bridge call with a static dict lookup, and force graceful degradation
    off (the real client short-circuits when the secret is unset — bypass that here by
    swapping the top-level function)."""
    def _fake(uuids):
        return {
            u: weespas_client.UserSummary(
                uuid=u,
                display_name=mapping[u].get("display_name", ""),
                avatar_url=mapping[u].get("avatar_url"),
                phone=mapping[u].get("phone"),
            )
            for u in uuids if u in mapping
        }
    monkeypatch.setattr(live_svc.weespas_client, "lookup_user_summaries", _fake)


# ---------------------------------------------------------------------------
# Service tests — get_hydrated_live_viewers
# ---------------------------------------------------------------------------

class TestGetHydratedLiveViewers:
    def test_returns_empty_when_no_live_rows(self, client, db_session):
        shop = _open_shop(client, "seller-a")
        rows = live_svc.get_hydrated_live_viewers(
            db_session, shop_id=shop["id"], now=datetime.now(timezone.utc),
        )
        assert rows == []

    def test_returns_empty_for_missing_shop_id(self, db_session):
        assert live_svc.get_hydrated_live_viewers(
            db_session, shop_id="", now=datetime.now(timezone.utc),
        ) == []

    def test_signed_in_viewer_hydrated_with_name_and_avatar(self, client, db_session, monkeypatch):
        shop = _open_shop(client, "seller-a")
        _stub_bridge(monkeypatch, {
            "viewer-1": {"display_name": "Alice", "avatar_url": "https://cdn/a.png"},
        })
        # Heartbeat as a signed-in viewer, with Kilimani coords.
        client.post(
            f"/api/v1/shops/{shop['id']}/heartbeat",
            json={"session_id": "s-1", "last_lat": _KILIMANI[0], "last_lng": _KILIMANI[1]},
            headers=_auth("viewer-1"),
        )
        rows = live_svc.get_hydrated_live_viewers(
            db_session, shop_id=shop["id"], now=datetime.now(timezone.utc),
        )
        assert len(rows) == 1
        r = rows[0]
        assert r.viewer_uuid == "viewer-1"
        assert r.display_name == "Alice"
        assert r.avatar_url == "https://cdn/a.png"
        assert r.area_label == "Kilimani"
        # Non-follower → phone withheld even if bridge had one.
        assert r.phone is None

    def test_anonymous_viewer_shown_as_guest(self, client, db_session, monkeypatch):
        shop = _open_shop(client, "seller-a")
        _stub_bridge(monkeypatch, {})   # bridge won't be called for anons
        client.post(
            f"/api/v1/shops/{shop['id']}/heartbeat",
            json={"session_id": "anon-1", "last_lat": _CBD[0], "last_lng": _CBD[1]},
        )
        rows = live_svc.get_hydrated_live_viewers(
            db_session, shop_id=shop["id"], now=datetime.now(timezone.utc),
        )
        assert len(rows) == 1
        assert rows[0].display_name == "Guest"
        assert rows[0].viewer_uuid is None
        assert rows[0].avatar_url is None
        assert rows[0].phone is None
        assert rows[0].area_label in ("CBD", "Central Business District", "Nairobi CBD")

    def test_bridge_miss_falls_back_to_guest(self, client, db_session, monkeypatch):
        """Signed-in viewer that weespas has no record for → labelled 'Guest' rather than
        exposing the bare uuid."""
        shop = _open_shop(client, "seller-a")
        _stub_bridge(monkeypatch, {})   # empty map: bridge returns nothing
        client.post(
            f"/api/v1/shops/{shop['id']}/heartbeat",
            json={"session_id": "s-1"},
            headers=_auth("viewer-ghost"),
        )
        rows = live_svc.get_hydrated_live_viewers(
            db_session, shop_id=shop["id"], now=datetime.now(timezone.utc),
        )
        assert len(rows) == 1
        assert rows[0].display_name == "Guest"

    def test_follower_gets_phone_exposed(self, client, db_session, monkeypatch):
        shop = _open_shop(client, "seller-a")
        _stub_bridge(monkeypatch, {
            "viewer-1": {"display_name": "Alice", "phone": "+254700000000"},
        })
        # Insert follow row directly (a full toggle-follow round trip isn't part of this test).
        db_session.add(ShopSubscription(user_uuid="viewer-1", shop_id=shop["id"]))
        db_session.commit()
        client.post(
            f"/api/v1/shops/{shop['id']}/heartbeat",
            json={"session_id": "s-1"},
            headers=_auth("viewer-1"),
        )
        rows = live_svc.get_hydrated_live_viewers(
            db_session, shop_id=shop["id"], now=datetime.now(timezone.utc),
        )
        assert rows[0].phone == "+254700000000"

    def test_non_follower_phone_withheld_even_when_bridge_returned_one(self, client, db_session, monkeypatch):
        shop = _open_shop(client, "seller-a")
        _stub_bridge(monkeypatch, {
            "viewer-1": {"display_name": "Alice", "phone": "+254700000000"},
        })
        # No ShopSubscription row → not following → phone withheld.
        client.post(
            f"/api/v1/shops/{shop['id']}/heartbeat",
            json={"session_id": "s-1"},
            headers=_auth("viewer-1"),
        )
        rows = live_svc.get_hydrated_live_viewers(
            db_session, shop_id=shop["id"], now=datetime.now(timezone.utc),
        )
        assert rows[0].phone is None

    def test_viewing_listing_title_hydrated(self, client, db_session, monkeypatch):
        shop = _open_shop(client, "seller-a")
        seller = db_session.query(Seller).filter_by(user_uuid="seller-a").one()
        listing = Listing(
            shop_id=shop["id"], seller_id=seller.id,
            title="Kikoi tote bag", price_cents=1000, stock_qty=5,
            lat=_LAT, lng=_LNG, is_active=True,
        )
        set_location(listing, _LAT, _LNG)
        db_session.add(listing)
        db_session.commit()
        _stub_bridge(monkeypatch, {"viewer-1": {"display_name": "Alice"}})
        client.post(
            f"/api/v1/shops/{shop['id']}/heartbeat",
            json={"session_id": "s-1", "viewing_listing_id": listing.id},
            headers=_auth("viewer-1"),
        )
        rows = live_svc.get_hydrated_live_viewers(
            db_session, shop_id=shop["id"], now=datetime.now(timezone.utc),
        )
        assert rows[0].viewing_listing_id == listing.id
        assert rows[0].viewing_listing_title == "Kikoi tote bag"

    def test_no_coords_yields_no_area_label(self, client, db_session, monkeypatch):
        shop = _open_shop(client, "seller-a")
        _stub_bridge(monkeypatch, {"viewer-1": {"display_name": "Alice"}})
        client.post(
            f"/api/v1/shops/{shop['id']}/heartbeat",
            json={"session_id": "s-1"},   # no coords
            headers=_auth("viewer-1"),
        )
        rows = live_svc.get_hydrated_live_viewers(
            db_session, shop_id=shop["id"], now=datetime.now(timezone.utc),
        )
        assert rows[0].area_label is None

    def test_stale_viewer_beyond_window_excluded(self, client, db_session, monkeypatch):
        shop = _open_shop(client, "seller-a")
        _stub_bridge(monkeypatch, {"viewer-1": {"display_name": "Alice"}})
        client.post(
            f"/api/v1/shops/{shop['id']}/heartbeat",
            json={"session_id": "s-1"},
            headers=_auth("viewer-1"),
        )
        # Query with a `now` well past the freshness window.
        future = datetime.now(timezone.utc) + timedelta(seconds=views_svc.LIVE_WINDOW_SECONDS * 4)
        rows = live_svc.get_hydrated_live_viewers(db_session, shop_id=shop["id"], now=future)
        assert rows == []

    def test_ordered_newest_heartbeat_first(self, client, db_session, monkeypatch):
        shop = _open_shop(client, "seller-a")
        _stub_bridge(monkeypatch, {
            "viewer-1": {"display_name": "Alice"},
            "viewer-2": {"display_name": "Bob"},
        })
        client.post(
            f"/api/v1/shops/{shop['id']}/heartbeat",
            json={"session_id": "s-1"}, headers=_auth("viewer-1"),
        )
        client.post(
            f"/api/v1/shops/{shop['id']}/heartbeat",
            json={"session_id": "s-2"}, headers=_auth("viewer-2"),
        )
        # s-1 refreshes → moves to the top.
        client.post(
            f"/api/v1/shops/{shop['id']}/heartbeat",
            json={"session_id": "s-1"}, headers=_auth("viewer-1"),
        )
        rows = live_svc.get_hydrated_live_viewers(
            db_session, shop_id=shop["id"], now=datetime.now(timezone.utc),
        )
        assert [r.display_name for r in rows] == ["Alice", "Bob"]


# ---------------------------------------------------------------------------
# Endpoint tests — GET /shops/{id}/live-viewers
# ---------------------------------------------------------------------------

class TestLiveViewersEndpoint:
    def test_owner_reads_hydrated_list(self, client, db_session, monkeypatch):
        shop = _open_shop(client, "seller-a")
        _stub_bridge(monkeypatch, {"viewer-1": {"display_name": "Alice"}})
        client.post(
            f"/api/v1/shops/{shop['id']}/heartbeat",
            json={"session_id": "s-1", "last_lat": _KILIMANI[0], "last_lng": _KILIMANI[1]},
            headers=_auth("viewer-1"),
        )
        r = client.get(f"/api/v1/shops/{shop['id']}/live-viewers", headers=_auth("seller-a"))
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["shop_id"] == shop["id"]
        assert body["count"] == 1
        assert body["window_seconds"] == views_svc.LIVE_WINDOW_SECONDS
        assert len(body["items"]) == 1
        it = body["items"][0]
        assert it["display_name"] == "Alice"
        assert it["area_label"] == "Kilimani"
        assert it["viewer_uuid"] == "viewer-1"

    def test_empty_when_quiet(self, client, monkeypatch):
        shop = _open_shop(client, "seller-a")
        _stub_bridge(monkeypatch, {})
        r = client.get(f"/api/v1/shops/{shop['id']}/live-viewers", headers=_auth("seller-a"))
        assert r.status_code == 200
        body = r.json()
        assert body["count"] == 0
        assert body["items"] == []

    def test_non_owner_404s(self, client, monkeypatch):
        shop = _open_shop(client, "seller-a")
        _stub_bridge(monkeypatch, {})
        r = client.get(f"/api/v1/shops/{shop['id']}/live-viewers", headers=_auth("seller-b"))
        assert r.status_code == 404

    def test_missing_token_401(self, client):
        shop = _open_shop(client, "seller-a")
        r = client.get(f"/api/v1/shops/{shop['id']}/live-viewers")
        assert r.status_code in (401, 403)

    def test_missing_scope_403s(self, client):
        shop = _open_shop(client, "seller-a")
        r = client.get(
            f"/api/v1/shops/{shop['id']}/live-viewers",
            headers=_auth("seller-a", scopes=("read:feed",)),
        )
        assert r.status_code == 403

    def test_missing_shop_404(self, client):
        r = client.get("/api/v1/shops/does-not-exist/live-viewers", headers=_auth("seller-a"))
        assert r.status_code == 404

    def test_bridge_outage_degrades_to_guest_not_500(self, client, monkeypatch):
        """The real weespas_client returns {} on any HTTP failure; simulate that here."""
        shop = _open_shop(client, "seller-a")
        monkeypatch.setattr(live_svc.weespas_client, "lookup_user_summaries", lambda uuids: {})
        client.post(
            f"/api/v1/shops/{shop['id']}/heartbeat",
            json={"session_id": "s-1"},
            headers=_auth("viewer-1"),
        )
        r = client.get(f"/api/v1/shops/{shop['id']}/live-viewers", headers=_auth("seller-a"))
        assert r.status_code == 200
        body = r.json()
        assert body["count"] == 1
        assert body["items"][0]["display_name"] == "Guest"

    def test_phone_exposed_only_to_followed_shop(self, client, db_session, monkeypatch):
        shop = _open_shop(client, "seller-a")
        _stub_bridge(monkeypatch, {
            "viewer-1": {"display_name": "Alice", "phone": "+254711111111"},
        })
        db_session.add(ShopSubscription(user_uuid="viewer-1", shop_id=shop["id"]))
        db_session.commit()
        client.post(
            f"/api/v1/shops/{shop['id']}/heartbeat",
            json={"session_id": "s-1"},
            headers=_auth("viewer-1"),
        )
        r = client.get(f"/api/v1/shops/{shop['id']}/live-viewers", headers=_auth("seller-a"))
        body = r.json()
        assert body["items"][0]["phone"] == "+254711111111"
