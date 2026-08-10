"""Flash Sales — the §8 nationwide "crazy offer" grid, on the SQLite path.

Asserts the properties that make the feature cheap and honest:
  * the craziness score is a bounded MARGIN vs comparable shops, computed once at launch, and a
    non-discount / bargain / out-of-bounds launch is rejected (422-shaped);
  * with zero comparable supply the reference degrades to the listing's own price (never a crash);
  * the read is NATIONWIDE — a sale in another city appears, ranked by score, geo ignored;
  * expiry is a PURE time filter (vanish, no sweep) and the price AUTO-RESTORES;
  * a buy during the window locks at the flash price and is untouched by later expiry;
  * ownership is enforced (cross-owner launch/clear → None → router 404);
  * the HTTP DTO is lean (no POS internals / PII) and lat/lng are bounded.
"""
import json
from datetime import datetime, timedelta, timezone

from PE.commerce.core.config import settings
from PE.commerce.models.listing import Listing
from PE.commerce.models.seller import Seller, Shop
from PE.commerce.services import flash_sales, proximity, settlement

_LAT, _LNG = -1.2921, 36.8219
# Somewhere far away (Kisumu ~ -0.09, 34.77) to prove the nationwide read ignores geography.
_FAR_LAT, _FAR_LNG = -0.0917, 34.7680


def _km_lat(km):
    return km / 111.32


def _seller(db, uuid="seller-1"):
    s = Seller(user_uuid=uuid, display_name="Mama Mboga")
    db.add(s)
    db.flush()
    return s


def _shop(db, seller, lat, lng, *, category=None, name="Corner Shop"):
    sh = Shop(seller_id=seller.id, name=name, category=category)
    proximity.set_location(sh, lat, lng)
    db.add(sh)
    db.flush()
    return sh


def _listing(db, shop, seller, lat, lng, *, title, price=10000, age_h=1.0, pricing="fixed",
             media=None, stock=10):
    li = Listing(
        shop_id=shop.id,
        seller_id=seller.id,
        title=title,
        price_cents=price,
        currency="KES",
        media_urls=json.dumps(media if media is not None else ["/uploads/trade/images/x.webp"]),
        intent_weight=1.0,
        is_active=True,
        stock_qty=stock,
        pricing_mode=pricing,
        created_at=datetime.now(timezone.utc) - timedelta(hours=age_h),
    )
    proximity.set_location(li, lat, lng)
    db.add(li)
    db.flush()
    return li


def _comparables(db, seller, *, category="shoes", price, n=3, title="Air Jordan sneaker"):
    """Seed n same-category comparable listings near the reference point at ``price``."""
    for i in range(n):
        lat = _LAT + _km_lat(1 + i)
        sh = _shop(db, seller, lat, _LNG, category=category)
        _listing(db, sh, seller, lat, _LNG, title=f"{title} {i}", price=price)


# --------------------------- score / launch ---------------------------

def test_score_is_the_margin_vs_comparables(db_session):
    """The stored score is the discount margin against the comparable average (crazier = higher)."""
    seller = _seller(db_session)
    _comparables(db_session, seller, category="shoes", price=10000)  # market ~ 10,000
    shop = _shop(db_session, seller, _LAT, _LNG, category="shoes")
    subject = _listing(db_session, shop, seller, _LAT, _LNG, title="Air Jordan crazy", price=10000)
    db_session.commit()

    listing = flash_sales.launch_flash_sale(
        db_session, "seller-1", str(subject.id), flash_price_cents=1000, duration_seconds=3600,
    )
    # 1000 vs a 10,000 reference ⇒ 90% margin.
    assert listing.flash_reference_cents == 10000
    assert round(listing.flash_score, 2) == 0.90
    assert listing.flash_price_cents == 1000
    # The NORMAL price is untouched (temporary override, not a mutation).
    assert listing.price_cents == 10000


def test_crazier_offer_outranks_milder_one(db_session):
    """Two flash sales; the one with the deeper discount ranks first in the nationwide read."""
    seller = _seller(db_session)
    _comparables(db_session, seller, category="shoes", price=10000)
    shop = _shop(db_session, seller, _LAT, _LNG, category="shoes")
    mild = _listing(db_session, shop, seller, _LAT, _LNG, title="Jordan mild", price=10000)
    crazy = _listing(db_session, shop, seller, _LAT, _LNG, title="Jordan crazy", price=10000)
    db_session.commit()

    flash_sales.launch_flash_sale(db_session, "seller-1", str(mild.id), flash_price_cents=8000, duration_seconds=3600)
    flash_sales.launch_flash_sale(db_session, "seller-1", str(crazy.id), flash_price_cents=500, duration_seconds=3600)

    rows = flash_sales.build_flash_sales(db_session)
    titles = [r.listing.title for r in rows]
    assert titles[0] == "Jordan crazy"  # deepest discount first
    assert titles.index("Jordan crazy") < titles.index("Jordan mild")


def test_non_discount_is_rejected(db_session):
    """A 'flash sale' priced at/above the comparable market is not a discount → FlashSaleError."""
    seller = _seller(db_session)
    _comparables(db_session, seller, category="shoes", price=10000)
    shop = _shop(db_session, seller, _LAT, _LNG, category="shoes")
    subject = _listing(db_session, shop, seller, _LAT, _LNG, title="not-crazy", price=10000)
    db_session.commit()

    try:
        flash_sales.launch_flash_sale(
            db_session, "seller-1", str(subject.id), flash_price_cents=10000, duration_seconds=3600,
        )
        assert False, "expected a non-discount to be rejected"
    except flash_sales.FlashSaleError:
        pass


def test_duration_and_price_bounds(db_session):
    seller = _seller(db_session)
    _comparables(db_session, seller, category="shoes", price=10000)
    shop = _shop(db_session, seller, _LAT, _LNG, category="shoes")
    subject = _listing(db_session, shop, seller, _LAT, _LNG, title="jordan", price=10000)
    db_session.commit()

    # > 1 hour is rejected (the hard cap).
    for bad_duration in (settings.flash_sales_max_duration_seconds + 1, 0):
        try:
            flash_sales.launch_flash_sale(
                db_session, "seller-1", str(subject.id), flash_price_cents=1000, duration_seconds=bad_duration,
            )
            assert False, f"expected duration {bad_duration} to be rejected"
        except flash_sales.FlashSaleError:
            pass
    # A non-positive price is rejected.
    try:
        flash_sales.launch_flash_sale(
            db_session, "seller-1", str(subject.id), flash_price_cents=0, duration_seconds=3600,
        )
        assert False, "expected price 0 to be rejected"
    except flash_sales.FlashSaleError:
        pass


def test_bargain_listing_cannot_flash(db_session):
    """A flash sale is a one-tap buy; a bargain listing (negotiated) can't run one."""
    seller = _seller(db_session)
    _comparables(db_session, seller, category="shoes", price=10000)
    shop = _shop(db_session, seller, _LAT, _LNG, category="shoes")
    subject = _listing(db_session, shop, seller, _LAT, _LNG, title="jordan", price=10000, pricing="bargain")
    db_session.commit()
    try:
        flash_sales.launch_flash_sale(
            db_session, "seller-1", str(subject.id), flash_price_cents=1000, duration_seconds=3600,
        )
        assert False, "expected a bargain listing to be rejected"
    except flash_sales.FlashSaleError:
        pass


def test_zero_comparables_falls_back_to_own_price(db_session):
    """With no comparable supply, the reference is the listing's own price — a launch still works
    (score computed off own price), and a genuine discount is accepted."""
    seller = _seller(db_session)
    shop = _shop(db_session, seller, _LAT, _LNG, category="shoes")  # the ONLY shoes shop
    subject = _listing(db_session, shop, seller, _LAT, _LNG, title="lonely jordan", price=10000)
    db_session.commit()

    listing = flash_sales.launch_flash_sale(
        db_session, "seller-1", str(subject.id), flash_price_cents=4000, duration_seconds=3600,
    )
    assert listing.flash_reference_cents == 10000  # own price is the reference
    assert round(listing.flash_score, 2) == 0.60


# --------------------------- nationwide read + expiry ---------------------------

def test_read_is_nationwide(db_session):
    """A flash sale created far away (Kisumu) still appears (geo ignored)."""
    seller = _seller(db_session)
    _comparables(db_session, seller, category="shoes", price=10000)
    far_shop = _shop(db_session, seller, _FAR_LAT, _FAR_LNG, category="shoes")
    far = _listing(db_session, far_shop, seller, _FAR_LAT, _FAR_LNG, title="Kisumu jordan", price=10000)
    db_session.commit()
    flash_sales.launch_flash_sale(db_session, "seller-1", str(far.id), flash_price_cents=1000, duration_seconds=3600)

    # A Nairobi buyer still sees the Kisumu sale.
    rows = flash_sales.build_flash_sales(db_session, lat=_LAT, lng=_LNG)
    assert any(r.listing.title == "Kisumu jordan" for r in rows)


def test_expiry_vanishes_and_price_restores(db_session):
    """An expired window drops from the read (pure time filter, no sweep) and active_flash_price
    returns None (the normal price is back)."""
    seller = _seller(db_session)
    _comparables(db_session, seller, category="shoes", price=10000)
    shop = _shop(db_session, seller, _LAT, _LNG, category="shoes")
    subject = _listing(db_session, shop, seller, _LAT, _LNG, title="jordan", price=10000)
    db_session.commit()
    flash_sales.launch_flash_sale(db_session, "seller-1", str(subject.id), flash_price_cents=1000, duration_seconds=3600)

    # Backdate the window into the past WITHOUT a sweep.
    now = datetime.now(timezone.utc)
    subject.flash_started_at = now - timedelta(seconds=7200)
    subject.flash_expires_at = now - timedelta(seconds=3600)
    db_session.commit()

    assert flash_sales.active_flash_price(subject) is None      # price reverted
    assert flash_sales.build_flash_sales(db_session) == []      # vanished from the read


def test_buy_locks_flash_price_in_window_then_normal_after(db_session):
    """A buy while the window is open locks at the flash price; after expiry a new order locks at
    the normal price (auto-restore)."""
    seller = _seller(db_session)
    _comparables(db_session, seller, category="shoes", price=10000)
    shop = _shop(db_session, seller, _LAT, _LNG, category="shoes")
    subject = _listing(db_session, shop, seller, _LAT, _LNG, title="jordan", price=10000)
    db_session.commit()
    flash_sales.launch_flash_sale(db_session, "seller-1", str(subject.id), flash_price_cents=1000, duration_seconds=3600)

    order = settlement.open_order(db_session, "buyer-A", str(subject.id), offer_cents=None, idem_key="k1")
    assert order.locked_price_cents == 1000  # locked at the flash price

    # Idempotent replay returns the SAME locked (flash) order even mid-window.
    replay = settlement.open_order(db_session, "buyer-A", str(subject.id), offer_cents=None, idem_key="k1")
    assert replay.id == order.id and replay.locked_price_cents == 1000

    # Expire the window; a DIFFERENT buyer's new order now locks at the normal price.
    now = datetime.now(timezone.utc)
    subject.flash_started_at = now - timedelta(seconds=7200)
    subject.flash_expires_at = now - timedelta(seconds=3600)
    db_session.commit()
    order2 = settlement.open_order(db_session, "buyer-B", str(subject.id), offer_cents=None, idem_key="k2")
    assert order2.locked_price_cents == 10000  # normal price restored


# --------------------------- ownership + clear ---------------------------

def test_cross_owner_launch_and_clear_return_none(db_session):
    seller = _seller(db_session)
    _comparables(db_session, seller, category="shoes", price=10000)
    shop = _shop(db_session, seller, _LAT, _LNG, category="shoes")
    subject = _listing(db_session, shop, seller, _LAT, _LNG, title="jordan", price=10000)
    db_session.commit()

    # A different user can neither launch nor clear (router maps None → 404, no existence leak).
    assert flash_sales.launch_flash_sale(
        db_session, "intruder", str(subject.id), flash_price_cents=1000, duration_seconds=3600,
    ) is None
    assert flash_sales.clear_flash_sale(db_session, "intruder", str(subject.id)) is None


def test_clear_is_idempotent(db_session):
    seller = _seller(db_session)
    _comparables(db_session, seller, category="shoes", price=10000)
    shop = _shop(db_session, seller, _LAT, _LNG, category="shoes")
    subject = _listing(db_session, shop, seller, _LAT, _LNG, title="jordan", price=10000)
    db_session.commit()
    flash_sales.launch_flash_sale(db_session, "seller-1", str(subject.id), flash_price_cents=1000, duration_seconds=3600)

    cleared = flash_sales.clear_flash_sale(db_session, "seller-1", str(subject.id))
    assert cleared.flash_price_cents is None and cleared.flash_expires_at is None
    # Clearing again is a clean no-op (still returns the listing, not an error).
    again = flash_sales.clear_flash_sale(db_session, "seller-1", str(subject.id))
    assert again is not None and again.flash_score is None


def test_relaunch_overwrites_and_recomputes(db_session):
    seller = _seller(db_session)
    _comparables(db_session, seller, category="shoes", price=10000)
    shop = _shop(db_session, seller, _LAT, _LNG, category="shoes")
    subject = _listing(db_session, shop, seller, _LAT, _LNG, title="jordan", price=10000)
    db_session.commit()

    first = flash_sales.launch_flash_sale(db_session, "seller-1", str(subject.id), flash_price_cents=5000, duration_seconds=3600)
    first_expiry = first.flash_expires_at
    assert round(first.flash_score, 2) == 0.50
    # Re-launch with a crazier price overwrites the window + recomputes the score.
    second = flash_sales.launch_flash_sale(db_session, "seller-1", str(subject.id), flash_price_cents=1000, duration_seconds=1800)
    assert round(second.flash_score, 2) == 0.90
    assert second.flash_expires_at != first_expiry


# --------------------------- HTTP edge ---------------------------

def test_http_returns_lean_dto_no_pii(client, db_session):
    seller = _seller(db_session)
    _comparables(db_session, seller, category="shoes", price=10000)
    shop = _shop(db_session, seller, _LAT, _LNG, category="shoes")
    subject = _listing(db_session, shop, seller, _LAT, _LNG, title="crazy jordan", price=10000)
    db_session.commit()
    flash_sales.launch_flash_sale(db_session, "seller-1", str(subject.id), flash_price_cents=1000, duration_seconds=3600)

    resp = client.get(f"/api/v1/flash-sales?lat={_LAT}&lng={_LNG}")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["page_size"] == settings.flash_sales_page_size
    assert body["items"], "expected the flash sale"
    item = body["items"][0]
    for banned in ("stock_qty", "intent_weight", "is_active", "buyer_uuid", "contact", "flash_score"):
        assert banned not in item
    assert {"id", "title", "flash_price_cents", "reference_cents", "discount_percent", "expires_at"} <= set(item)
    assert item["discount_percent"] == 90


def test_http_works_without_location(client, db_session):
    """The location is optional — the grid still returns (distance just null)."""
    seller = _seller(db_session)
    _comparables(db_session, seller, category="shoes", price=10000)
    shop = _shop(db_session, seller, _LAT, _LNG, category="shoes")
    subject = _listing(db_session, shop, seller, _LAT, _LNG, title="crazy jordan", price=10000)
    db_session.commit()
    flash_sales.launch_flash_sale(db_session, "seller-1", str(subject.id), flash_price_cents=1000, duration_seconds=3600)

    resp = client.get("/api/v1/flash-sales")
    assert resp.status_code == 200, resp.text
    assert resp.json()["items"][0]["distance_m"] is None


def test_http_rejects_out_of_range_latlng(client):
    assert client.get("/api/v1/flash-sales?lat=999&lng=0").status_code == 422
    assert client.get("/api/v1/flash-sales?lat=0&lng=999").status_code == 422
