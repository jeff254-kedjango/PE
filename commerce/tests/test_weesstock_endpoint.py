"""GET /sellers/me/credit-profile (§WeesStock F2) — the seller's own credit profile.

Real RS256 tokens, mirroring test_receipts.py. What is defended here is the ENDPOINT's
contract; the scoring maths itself is covered exhaustively in test_credit_score.py.

The security properties under test:
  * self-view only — there is no id parameter, and one seller's token can never surface
    another seller's numbers;
  * no buyer PII in the payload, ever (this shape is what a financier eventually sees);
  * scope-gated like every other seller endpoint.
"""
from datetime import datetime, timedelta, timezone
from pathlib import Path

import jwt
import pytest
from fastapi.testclient import TestClient

from PE.commerce.core.database import get_db
from PE.commerce.main import app
from PE.commerce.models.listing import Listing
from PE.commerce.models.order import Order, STATUS_CANCELLED, STATUS_SETTLED
from PE.commerce.models.receipt import Receipt
from PE.commerce.models.seller import Seller, Shop
from PE.commerce.services import credit_score as cs

_KEYS = Path(__file__).resolve().parents[3] / "PE" / "dev" / "keys"
_PRIVATE = (_KEYS / "insar_jwt_private.pem").read_text()
_SELLER_SCOPES = ("read:feed", "create:trades")
_LAT, _LNG = -1.2920, 36.8219
_URL = "/api/v1/sellers/me/credit-profile"


def _mint(sub, scopes):
    return jwt.encode(
        {"sub": sub, "role": "user", "scope": "commerce_trade", "scopes": list(scopes),
         "exp": datetime.now(timezone.utc) + timedelta(minutes=10)},
        _PRIVATE, algorithm="RS256",
    )


def _auth(sub="seller-1", scopes=_SELLER_SCOPES):
    return {"Authorization": f"Bearer {_mint(sub, scopes)}"}


@pytest.fixture
def client(db_session):
    app.dependency_overrides.clear()

    def _override_db():
        yield db_session

    app.dependency_overrides[get_db] = _override_db
    yield TestClient(app, raise_server_exceptions=False)
    app.dependency_overrides.clear()


def _seed_seller(db, *, sub="seller-1", tenure_days=400.0):
    s = Seller(user_uuid=sub, display_name=f"Seller {sub}",
               created_at=datetime.now(timezone.utc) - timedelta(days=tenure_days))
    db.add(s)
    db.flush()
    sh = Shop(seller_id=s.id, name="Mama Mboga", lat=_LAT, lng=_LNG)
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


class TestAuth:
    def test_requires_a_token(self, client):
        assert client.get(_URL).status_code in (401, 403)

    def test_read_only_token_is_refused(self, client):
        """Same gate as every other seller endpoint: create:trades, not just read:feed."""
        r = client.get(_URL, headers=_auth(scopes=("read:feed",)))
        assert r.status_code == 403


class TestNewSeller:
    def test_caller_with_no_seller_row_gets_an_empty_profile_not_a_404(self, client):
        """A brand-new user must see "no history yet", not an error card."""
        r = client.get(_URL, headers=_auth(sub="never-sold"))
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["score"] is None
        assert body["is_scoreable"] is False
        assert body["revenue_cents"] == 0
        assert body["settled_orders"] == 0
        assert set(body["missing_for_score"]) == {"settled_orders", "tenure"}
        assert body["orders_needed"] == cs.MIN_ORDERS_FOR_SCORE

    def test_empty_profile_is_not_persisted(self, client, db_session):
        """The handler synthesises a transient Seller for the scorer; it must never be
        written. A phantom seller row would corrupt every downstream seller query."""
        client.get(_URL, headers=_auth(sub="never-sold"))
        assert db_session.query(Seller).filter(
            Seller.user_uuid == "never-sold").one_or_none() is None


class TestThinFile:
    def test_components_present_while_score_withheld(self, client, db_session):
        seller, li = _seed_seller(db_session)
        for i in range(3):
            _sell(db_session, seller, li, buyer=f"b{i}")

        body = client.get(_URL, headers=_auth()).json()
        assert body["score"] is None
        assert body["settled_orders"] == 3
        assert body["revenue_cents"] == 3 * 19_400
        # The seller can still see which components are already strong.
        assert any(c["weighted"] > 0 for c in body["components"])
        assert body["orders_needed"] == cs.MIN_ORDERS_FOR_SCORE - 3
        assert body["days_needed"] == 0

    def test_score_is_null_not_zero(self, client, db_session):
        """JSON null and 0.0 are different states; a client that coerces would tell a healthy
        new shop it is uncreditworthy."""
        _seed_seller(db_session)
        assert client.get(_URL, headers=_auth()).json()["score"] is None


class TestScoredProfile:
    def test_full_profile_shape(self, client, db_session):
        seller, li = _seed_seller(db_session)
        for i in range(12):
            _sell(db_session, seller, li, buyer=f"b{i % 5}", gross=20_000)

        r = client.get(_URL, headers=_auth())
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["is_scoreable"] is True
        assert 0.0 <= body["score"] <= 1.0
        assert body["missing_for_score"] == []
        assert body["currency"] == "KES"
        assert body["settled_orders"] == 12
        assert body["unique_buyers"] == 5
        assert body["window_days"] == cs.REVENUE_WINDOW_DAYS
        assert body["recent_window_days"] == cs.RECENT_WINDOW_DAYS

    def test_components_sum_to_the_score(self, client, db_session):
        """Explainability is contractual: if the breakdown doesn't add up, the card lies."""
        seller, li = _seed_seller(db_session)
        for i in range(12):
            _sell(db_session, seller, li, buyer=f"b{i % 4}", gross=30_000)

        body = client.get(_URL, headers=_auth()).json()
        assert sum(c["weighted"] for c in body["components"]) == pytest.approx(body["score"])
        assert sum(c["weight"] for c in body["components"]) == pytest.approx(1.0)

    def test_failed_orders_are_reported(self, client, db_session):
        seller, li = _seed_seller(db_session)
        for i in range(10):
            _sell(db_session, seller, li, buyer=f"b{i}")
        db_session.add(Order(listing_id=li.id, seller_id=seller.id, buyer_uuid="x",
                             pricing_mode="fixed", status=STATUS_CANCELLED,
                             reference_price_cents=20_000,
                             created_at=datetime.now(timezone.utc) - timedelta(days=2)))
        db_session.flush()

        body = client.get(_URL, headers=_auth()).json()
        assert body["failed_orders"] == 1
        assert body["fulfilment_rate"] == pytest.approx(10 / 11)


class TestIsolationAndPrivacy:
    def test_one_seller_never_sees_another_sellers_numbers(self, client, db_session):
        """Self-view only. There is no id parameter to tamper with, and the seller row is
        resolved from the verified token sub."""
        mine, my_li = _seed_seller(db_session, sub="seller-mine")
        theirs, their_li = _seed_seller(db_session, sub="seller-theirs")
        _sell(db_session, mine, my_li, buyer="b1", gross=20_000)
        for i in range(30):
            _sell(db_session, theirs, their_li, buyer=f"t{i}", gross=900_000)

        body = client.get(_URL, headers=_auth(sub="seller-mine")).json()
        assert body["settled_orders"] == 1
        assert body["revenue_cents"] == 19_400

    def test_payload_carries_no_buyer_identities(self, client, db_session):
        """Hard boundary: buyers are third parties who never consented to appear in a
        seller's funding application. This same shape is what a financier sees in F4, so the
        guarantee is asserted against the RAW body, not a parsed subset."""
        seller, li = _seed_seller(db_session)
        for i in range(12):
            _sell(db_session, seller, li, buyer=f"buyer-uuid-{i}", gross=20_000)

        raw = client.get(_URL, headers=_auth()).text
        assert "buyer-uuid-" not in raw
        # Buyer *counts* are fine and expected; buyer *identities* are not.
        body = client.get(_URL, headers=_auth()).json()
        assert body["unique_buyers"] == 12
        for banned in ("buyer_uuid", "buyers", "receipts", "orders", "listing_title"):
            assert banned not in body

    def test_no_receipt_level_detail_is_exposed(self, client, db_session):
        """Aggregates only — never per-order line items (a locked decision for F4 parity)."""
        seller, li = _seed_seller(db_session)
        _sell(db_session, seller, li, buyer="b1", gross=20_000)

        body = client.get(_URL, headers=_auth()).json()
        assert all(not isinstance(v, list) or k == "components" or k == "missing_for_score"
                   for k, v in body.items())
