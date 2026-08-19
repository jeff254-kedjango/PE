"""WeesStock market endpoints (§WeesStock F4) — the investor discovery/analytics surface.

Real RS256 tokens, mirroring test_weesstock_endpoint.py. What is defended here is the
ENDPOINT contract of the consent-gated investor surface:

  1. **Consent is the boundary.** The list returns opt-in sellers only, and the detail view
     404s for BOTH an unknown id and an unlisted seller — probing never confirms that an
     unlisted seller exists (S6).
  2. **Read vs write scope.** Investors browse with the ordinary audience token (same class
     as the feed); only the seller's own consent switch needs create:trades.
  3. **Aggregates only, no PII.** Same privacy shape as the credit profile — never buyer
     identities, never per-order lines.
  4. **Parity.** The market's numbers are computed by the SAME scorer the seller's own card
     uses — a market and a seller card that disagree would be a lie in one of two places.
  5. **Discovery only.** The surface transacts nothing.
"""
from datetime import datetime, timedelta, timezone
from pathlib import Path

import jwt
import pytest
from fastapi.testclient import TestClient

from PE.commerce.core.database import get_db
from PE.commerce.main import app
from PE.commerce.models.listing import Listing
from PE.commerce.models.order import Order, STATUS_SETTLED
from PE.commerce.models.receipt import Receipt
from PE.commerce.models.seller import Seller, Shop

_KEYS = Path(__file__).resolve().parents[3] / "PE" / "dev" / "keys"
_PRIVATE = (_KEYS / "insar_jwt_private.pem").read_text()
_BUYER_SCOPES = ("read:feed",)
_SELLER_SCOPES = ("read:feed", "create:trades")
_LAT, _LNG = -1.2920, 36.8219
_LIST_URL = "/api/v1/weesstock/markets"
_TOGGLE_URL = "/api/v1/weesstock/me/listing"


def _mint(sub, scopes):
    return jwt.encode(
        {"sub": sub, "role": "user", "scope": "commerce_trade", "scopes": list(scopes),
         "exp": datetime.now(timezone.utc) + timedelta(minutes=10)},
        _PRIVATE, algorithm="RS256",
    )


def _auth(sub="buyer-1", scopes=_BUYER_SCOPES):
    return {"Authorization": f"Bearer {_mint(sub, scopes)}"}


@pytest.fixture
def client(db_session):
    app.dependency_overrides.clear()

    def _override_db():
        yield db_session

    app.dependency_overrides[get_db] = _override_db
    yield TestClient(app, raise_server_exceptions=False)
    app.dependency_overrides.clear()


def _seed_seller(db, *, sub="seller-1", tenure_days=400.0, listed=False, shop_name="Mama Mboga"):
    s = Seller(user_uuid=sub, display_name=f"Seller {sub}",
               created_at=datetime.now(timezone.utc) - timedelta(days=tenure_days),
               weesstock_listed=listed)
    db.add(s)
    db.flush()
    sh = Shop(seller_id=s.id, name=shop_name, lat=_LAT, lng=_LNG, category="retail")
    db.add(sh)
    db.flush()
    li = Listing(shop_id=sh.id, seller_id=s.id, title="Maize flour 2kg",
                 price_cents=12000, stock_qty=10, lat=_LAT, lng=_LNG)
    db.add(li)
    db.flush()
    return s, li


def _sell(db, seller, listing, *, buyer, gross=20_000, days_ago=5, receipt=True):
    when = datetime.now(timezone.utc) - timedelta(days=days_ago)
    commission = gross * 3 // 100
    o = Order(listing_id=listing.id, seller_id=seller.id, buyer_uuid=buyer,
              pricing_mode="fixed", status=STATUS_SETTLED,
              reference_price_cents=gross, locked_price_cents=gross,
              commission_cents=commission, created_at=when)
    db.add(o)
    db.flush()
    if receipt:
        db.add(Receipt(order_id=o.id, buyer_uuid=buyer, seller_id=seller.id,
                       listing_id=listing.id, listing_title=listing.title, currency="KES",
                       gross_cents=gross, commission_cents=commission,
                       net_to_seller_cents=gross - commission,
                       chain_tip_hash="0" * 64, receipt_hash=f"h{o.id}"[:64],
                       issued_at=when))
        db.flush()
    return o


class TestAuthAndScope:
    def test_market_list_requires_a_token(self, client):
        assert client.get(_LIST_URL).status_code in (401, 503)

    def test_investors_need_only_the_audience_scope(self, client, db_session):
        """Browsing the market is a READ — the same token class as the feed, NOT create:trades.
        A financier is not a seller and must not need seller scope to look."""
        s, li = _seed_seller(db_session, listed=True)
        for i in range(12):
            _sell(db_session, s, li, buyer=f"b{i % 4}")
        r = client.get(_LIST_URL, headers=_auth())          # buyer scopes only
        assert r.status_code == 200, r.text
        assert len(r.json()["entries"]) == 1

    def test_consent_switch_requires_seller_scope(self, client):
        assert client.post(_TOGGLE_URL, json={"listed": True}, headers=_auth()).status_code == 403

    def test_consent_switch_without_a_token(self, client):
        assert client.post(_TOGGLE_URL, json={"listed": True}).status_code in (401, 503)


class TestConsentBoundary:
    def test_unlisted_seller_is_absent_from_the_list(self, client, db_session):
        _seed_seller(db_session, listed=False)
        body = client.get(_LIST_URL, headers=_auth()).json()
        assert body["entries"] == []

    def test_listed_seller_appears(self, client, db_session):
        s, li = _seed_seller(db_session, listed=True)
        for i in range(12):
            _sell(db_session, s, li, buyer=f"b{i % 4}")
        entries = client.get(_LIST_URL, headers=_auth()).json()["entries"]
        assert len(entries) == 1
        assert entries[0]["seller_name"] == "Seller seller-1"
        assert entries[0]["shop_name"] == "Mama Mboga"
        assert entries[0]["category"] == "retail"

    def test_detail_404s_for_unlisted_and_unknown_alike(self, client, db_session):
        """No existence leak: an existing-but-unlisted seller must be indistinguishable from
        a garbage id."""
        s, _ = _seed_seller(db_session, listed=False)
        listed_id = str(s.id)
        garbage = "00000000-0000-0000-0000-000000000000"
        assert client.get(f"{_LIST_URL}/{listed_id}", headers=_auth()).status_code == 404
        assert client.get(f"{_LIST_URL}/{garbage}", headers=_auth()).status_code == 404

    def test_detail_serves_a_listed_seller(self, client, db_session):
        s, li = _seed_seller(db_session, listed=True)
        for i in range(12):
            _sell(db_session, s, li, buyer=f"b{i % 4}")
        r = client.get(f"{_LIST_URL}/{s.id}", headers=_auth())
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["seller"]["seller_name"] == "Seller seller-1"
        assert body["profile"]["is_scoreable"] is True

    def test_toggle_lists_and_unlists_own_seller(self, client, db_session):
        s, li = _seed_seller(db_session, listed=False)
        for i in range(12):
            _sell(db_session, s, li, buyer=f"b{i % 4}")

        r = client.post(_TOGGLE_URL, json={"listed": True}, headers=_auth(sub="seller-1", scopes=_SELLER_SCOPES))
        assert r.status_code == 200 and r.json()["listed"] is True
        assert len(client.get(_LIST_URL, headers=_auth()).json()["entries"]) == 1

        client.post(_TOGGLE_URL, json={"listed": False}, headers=_auth(sub="seller-1", scopes=_SELLER_SCOPES))
        assert client.get(_LIST_URL, headers=_auth()).json()["entries"] == []

    def test_consent_read_returns_the_persisted_state(self, client, db_session):
        """The seller's switch must render from the SERVER's current state, never a client
        guess. Default-off is the honest first paint; a stored True round-trips back."""
        s, li = _seed_seller(db_session, listed=False)
        for i in range(12):
            _sell(db_session, s, li, buyer=f"b{i % 4}")
        auth = _auth(sub="seller-1", scopes=_SELLER_SCOPES)

        assert client.get(_TOGGLE_URL, headers=auth).json()["listed"] is False
        client.post(_TOGGLE_URL, json={"listed": True}, headers=auth)
        assert client.get(_TOGGLE_URL, headers=auth).json()["listed"] is True

    def test_consent_read_is_owner_only(self, client, db_session):
        """Reading someone else's consent is as impossible as writing it — there is no id
        parameter, so the read is always the token's own row. A non-seller caller gets the
        same uniform 404 as on the write half."""
        _seed_seller(db_session, listed=True)
        r = client.get(_TOGGLE_URL, headers=_auth(sub="someone-else", scopes=_SELLER_SCOPES))
        assert r.status_code == 404

    def test_toggle_only_affects_the_callers_own_seller(self, client, db_session):
        """There is no id parameter — consent can only ever be flipped for the token's own
        seller row. Other sellers' flags must be untouched."""
        a, la = _seed_seller(db_session, sub="seller-a", listed=False)
        b, lb = _seed_seller(db_session, sub="seller-b", listed=False)
        for i in range(12):
            _sell(db_session, a, la, buyer=f"a{i % 4}")
            _sell(db_session, b, lb, buyer=f"b{i % 4}")

        client.post(_TOGGLE_URL, json={"listed": True}, headers=_auth(sub="seller-a", scopes=_SELLER_SCOPES))

        rows = db_session.query(Seller).filter(Seller.user_uuid.in_(["seller-a", "seller-b"])).all()
        flags = {r.user_uuid: r.weesstock_listed for r in rows}
        assert flags == {"seller-a": True, "seller-b": False}

    def test_nonseller_cannot_consent(self, client):
        """A caller with no seller row has nothing to list — uniform 404, not a created row."""
        r = client.post(_TOGGLE_URL, json={"listed": True},
                        headers=_auth(sub="never-sold", scopes=_SELLER_SCOPES))
        assert r.status_code == 404


class TestPayloadAndParity:
    def test_list_score_matches_the_sellers_own_card(self, client, db_session):
        """Parity is contractual: the investor list and the seller's own card must show the
        SAME score — computed by the same scorer, or one of the two is lying."""
        s, li = _seed_seller(db_session, listed=True)
        for i in range(12):
            _sell(db_session, s, li, buyer=f"b{i % 4}")

        market_score = client.get(_LIST_URL, headers=_auth()).json()["entries"][0]["score"]
        own = client.get("/api/v1/sellers/me/credit-profile",
                         headers=_auth(sub="seller-1", scopes=_SELLER_SCOPES)).json()
        assert market_score == pytest.approx(own["score"])

    def test_list_carries_no_buyer_identities(self, client, db_session):
        s, li = _seed_seller(db_session, listed=True)
        for i in range(12):
            _sell(db_session, s, li, buyer=f"buyer-uuid-{i}")

        raw = client.get(_LIST_URL, headers=_auth()).text
        assert "buyer-uuid-" not in raw
        for banned in ("buyer_uuid", "receipts", "orders", "listing_title"):
            assert banned not in raw

    def test_series_buckets_are_weekly_and_aligned_to_now(self, client, db_session):
        """A sale 3 days ago lands in the CURRENT (last) bucket; a sale 40 days ago in the
        bucket 40//7=5 slots earlier. Pins the bucket arithmetic the sparkline depends on."""
        s, li = _seed_seller(db_session, listed=True)
        _sell(db_session, s, li, buyer="recent", gross=20_000, days_ago=3)
        _sell(db_session, s, li, buyer="older", gross=30_000, days_ago=40)

        series = client.get(_LIST_URL, headers=_auth()).json()["entries"][0]["series"]
        assert series["bucket_count"] == 13
        assert len(series["series_cents"]) == 13
        assert series["bucket_days"] == 7
        # recent → last bucket (19_400 net); older → bucket 13-1-5 = 7 (29_100 net).
        assert series["series_cents"][-1] == 19_400
        assert series["series_cents"][7] == 29_100
        assert sum(series["series_cents"]) == 19_400 + 29_100

    def test_detail_profile_is_the_full_credit_shape(self, client, db_session):
        s, li = _seed_seller(db_session, listed=True)
        for i in range(12):
            _sell(db_session, s, li, buyer=f"b{i % 4}")

        body = client.get(f"{_LIST_URL}/{s.id}", headers=_auth()).json()
        profile = body["profile"]
        assert set(profile) >= {"score", "is_scoreable", "components", "revenue_cents",
                                "revenue_trend", "window_days"}
        assert sum(c["weighted"] for c in profile["components"]) == pytest.approx(profile["score"])
        assert len(body["series"]["series_cents"]) == 13

    def test_strongest_seller_comes_first(self, client, db_session):
        weak, weak_li = _seed_seller(db_session, sub="seller-weak", listed=True)
        strong, strong_li = _seed_seller(db_session, sub="seller-strong", listed=True)
        for i in range(12):
            _sell(db_session, weak, weak_li, buyer=f"w{i % 4}", gross=20_000)
        for i in range(30):
            _sell(db_session, strong, strong_li, buyer=f"s{i % 3}", gross=900_000)

        names = [e["seller_name"] for e in client.get(_LIST_URL, headers=_auth()).json()["entries"]]
        assert names == ["Seller seller-strong", "Seller seller-weak"]
