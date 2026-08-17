"""WeesStock credit seeder — scripts/seed_weesstock_credit.py.

A dev-only script, but it writes MONEY-SHAPED rows (settled orders and hash-chained receipts)
through the real service path, so the properties that keep it from becoming a liability are
asserted rather than assumed:

  1. **Production gate.** Refuses to seed or purge under production. This writes fabricated
     receipts; no environment flag should be able to authorize that against a real ledger.
  2. **Backdating does not break the hash chains.** The script rewrites ``Order.created_at`` and
     ``Receipt.issued_at`` after settlement — the one thing it does outside the services. The
     whole approach rests on no hash covering a timestamp, so every seeded receipt hash and every
     order-event chain is RECOMPUTED here and compared. If someone ever adds a timestamp to either
     canonical string, this test fails and the seeder must change.
  3. **Both timestamp columns move together.** The scorer windows receipts and orders
     independently; moving one without the other yields a shop with revenue but no orders, which
     cannot exist and would misreport fulfilment.
  4. **Idempotent.** A second run must replay, not double the revenue — the property that makes a
     seeder safe to re-run against a live dev DB.
  5. **Sales never predate the seller**, and land inside the scorer's revenue window.
  6. **The spread is real.** Tiers must produce genuinely different profiles, including at least
     one scored-but-weak shop and the two cold-start branches.
  7. **Purge is prefix-scoped** — a real seller's orders/receipts/reviews survive it.
"""
import hashlib
from collections import Counter
from datetime import datetime, timedelta, timezone

import pytest

from PE.commerce.models.listing import Listing
from PE.commerce.models.order import STATUS_CANCELLED, STATUS_SETTLED, Order, OrderEvent
from PE.commerce.models.receipt import Receipt
from PE.commerce.models.review import Review
from PE.commerce.models.seller import Seller, Shop
from PE.commerce.scripts import seed_weesstock_credit as seeder
from PE.commerce.services import credit_score
from PE.commerce.services.credit_score import MIN_ORDERS_FOR_SCORE, MIN_TENURE_DAYS

NOW = datetime(2026, 8, 14, 12, 0, tzinfo=timezone.utc)
_LAT, _LNG = -1.2921, 36.8219


def _seller(db, sub, name, *, tenure_days=45):
    s = Seller(user_uuid=sub, display_name=name,
               created_at=NOW - timedelta(days=tenure_days))
    db.add(s)
    db.flush()
    return s


def _shop(db, seller, name="Shop"):
    sh = Shop(seller_id=seller.id, name=name, lat=_LAT, lng=_LNG)
    db.add(sh)
    db.flush()
    return sh


def _listing(db, shop, *, price, mode="fixed", title="Thing", active=True, kind="product"):
    li = Listing(shop_id=shop.id, seller_id=shop.seller_id, title=title,
                 price_cents=price, currency="KES", pricing_mode=mode, stock_qty=99,
                 is_active=active, post_kind=kind, lat=_LAT, lng=_LNG)
    db.add(li)
    db.flush()
    return li


@pytest.fixture
def dev(monkeypatch):
    """Put the settings singleton in development for the duration of a test."""
    monkeypatch.setattr(seeder.settings, "commerce_env", "development")


@pytest.fixture
def shop_with_catalogue(db_session, dev):
    """One seller with a mixed catalogue: fixed listings to sell, one bargain to fail on."""
    s = _seller(db_session, "real-seller-1", "Alpha Traders", tenure_days=45)
    sh = _shop(db_session, s, "Alpha Shop")
    _listing(db_session, sh, price=120_000, title="A")
    _listing(db_session, sh, price=80_000, title="B")
    _listing(db_session, sh, price=200_000, mode="bargain", title="C")
    db_session.commit()
    return db_session, s


class TestProductionGate:
    def test_seed_refuses_in_production(self, db_session, monkeypatch):
        """The load-bearing safety property: fabricated receipts must be impossible to write
        against a real ledger, whatever the environment says."""
        monkeypatch.setattr(seeder.settings, "commerce_env", "production")
        with pytest.raises(RuntimeError, match="production"):
            seeder.seed(db_session, now=NOW)

    def test_purge_refuses_in_production(self, db_session, monkeypatch):
        monkeypatch.setattr(seeder.settings, "commerce_env", "production")
        with pytest.raises(RuntimeError, match="production"):
            seeder.purge(db_session)


class TestCatalogueDiscovery:
    def test_only_priced_active_products_are_sellable(self, db_session, dev):
        """A price-0 row would settle for nothing yet still count toward the cold-start ORDER gate
        — inflating the gate on sales that never moved money. Timeline posts and deactivated
        listings are not merchandise either."""
        s = _seller(db_session, "s1", "Alpha")
        sh = _shop(db_session, s)
        good = _listing(db_session, sh, price=50_000, title="sellable")
        _listing(db_session, sh, price=0, title="free")
        _listing(db_session, sh, price=50_000, title="post", kind="post")
        _listing(db_session, sh, price=50_000, title="gone", active=False)
        db_session.commit()

        fixed, bargain = seeder._sellable(db_session, str(s.id))
        assert [x.id for x in fixed] == [good.id]
        assert bargain == []

    def test_seller_with_nothing_sellable_is_excluded(self, db_session, dev):
        """Eva Mokaya's live shape: a price-0 timeline post and nothing else. She must be skipped,
        not given a fabricated business (user decision 2026-08-14)."""
        s = _seller(db_session, "s-eva", "Eva")
        sh = _shop(db_session, s, "My timeline")
        _listing(db_session, sh, price=0, title="post", kind="post")
        db_session.commit()

        assert seeder._candidates(db_session) == []
        assert "Eva" in [name for _seller, name in seeder._skipped_sellers(db_session)]

    def test_biggest_catalogue_gets_the_strongest_tier(self, db_session, dev):
        """Tier assignment is by DESCENDING sellable count, so the biggest catalogue reads as the
        strongest business — what a human eyeballing the console expects."""
        big = _seller(db_session, "s-big", "Big")
        small = _seller(db_session, "s-small", "Small")
        bs, ss = _shop(db_session, big, "B"), _shop(db_session, small, "S")
        for i in range(4):
            _listing(db_session, bs, price=10_000 + i, title=f"b{i}")
        _listing(db_session, ss, price=10_000, title="s0")
        db_session.commit()

        names = [s.display_name for s, _f, _b in seeder._candidates(db_session)]
        assert names == ["Big", "Small"]

    def test_trending_pool_and_thin_demo_sellers_are_excluded(self, db_session, dev):
        """The 50 synthetic trending shops are rail filler nobody logs in as, and the thin demo
        sellers are seeded separately with a fixed shape — neither may absorb a real tier."""
        for sub in ("demo-trending-pool-3", f"{seeder.THIN_PREFIX}a"):
            s = _seller(db_session, sub, f"Demo {sub}")
            _listing(db_session, _shop(db_session, s), price=50_000)
        db_session.commit()
        assert seeder._candidates(db_session) == []


class TestBuyerSequence:
    """Pure-function tests for the buyer mix — the repeat-rate dial, checked without a DB."""

    def test_one_timers_buy_exactly_once_and_regulars_come_back(self):
        seq = seeder._buyer_sequence(0, 20, regulars=4, one_timers=10)
        counts = Counter(seq)

        assert len(seq) == 20
        assert len(counts) == 14, "10 one-timers + 4 regulars"
        assert sum(1 for b, n in counts.items() if n == 1) == 10
        assert sum(1 for b, n in counts.items() if n > 1) == 4

    def test_repeat_rate_is_the_ratio_the_tier_asked_for(self):
        """What the scorer will compute: repeat / unique."""
        counts = Counter(seeder._buyer_sequence(0, 46, regulars=9, one_timers=12))
        repeat = sum(1 for n in counts.values() if n > 1)
        assert repeat / len(counts) == pytest.approx(9 / 21)

    def test_is_deterministic(self):
        """No RNG: re-runs must reproduce the same profile, or idempotency is meaningless."""
        assert seeder._buyer_sequence(3, 30, regulars=5, one_timers=16) == \
            seeder._buyer_sequence(3, 30, regulars=5, one_timers=16)

    def test_more_one_timers_than_sales_is_truncated_not_overrun(self):
        """A thin seller has fewer sales than one-timers. It must still return exactly `sales`
        entries — a longer list would index past the end at the call site."""
        assert len(seeder._buyer_sequence(0, 3, regulars=2, one_timers=99)) == 3

    def test_zero_regulars_is_rejected(self):
        """Silently pinning the repeat term to 0.0 is exactly the class of bug this replaced."""
        with pytest.raises(ValueError, match="regular"):
            seeder._buyer_sequence(0, 10, regulars=0, one_timers=10)

    def test_every_tier_produces_a_rate_strictly_between_0_and_1(self):
        """The shipped table, not a hypothetical one: no rung may pin the component."""
        for tier in seeder.TIERS:
            counts = Counter(seeder._buyer_sequence(
                0, tier.settled, regulars=tier.regulars, one_timers=tier.one_timers))
            rate = sum(1 for n in counts.values() if n > 1) / len(counts)
            assert 0.0 < rate < 1.0, f"{tier.name} pins repeat rate at {rate}"


class TestOneSale:
    def test_settled_sale_produces_a_receipt_with_consistent_money(self, shop_with_catalogue):
        """Receipts are the scorer's only revenue source, so the money invariant
        (net = gross - commission) must hold on the rows the seeder creates."""
        db, seller = shop_with_catalogue
        fixed, _ = seeder._sellable(db, str(seller.id))

        order = seeder._settle_one(db, seller, fixed[0], buyer="demo-weesstock-buyer-0", index=0)
        assert order is not None and order.status == STATUS_SETTLED

        receipt = db.query(Receipt).filter(Receipt.order_id == order.id).one()
        assert receipt.gross_cents == fixed[0].price_cents
        assert receipt.net_to_seller_cents == receipt.gross_cents - receipt.commission_cents
        assert receipt.currency == "KES"

    def test_failed_order_uses_the_bargain_path(self, shop_with_catalogue):
        """A fixed order jumps straight to PRICE_LOCKED, which is not in OPEN_STATUSES, so
        cancel() refuses it — a dead negotiation is the only honest way to make a failed order."""
        db, seller = shop_with_catalogue
        _, bargain = seeder._sellable(db, str(seller.id))

        order = seeder._fail_one(db, seller, bargain[0], buyer="demo-weesstock-buyer-1", index=0)
        assert order is not None and order.status == STATUS_CANCELLED

    def test_fixed_listing_cannot_produce_a_failed_order(self, shop_with_catalogue):
        """Pins the constraint the seeder is built around. If the state machine ever allowed
        cancelling a locked order this would change, and _fail_one's design should be revisited."""
        db, seller = shop_with_catalogue
        fixed, _ = seeder._sellable(db, str(seller.id))
        assert seeder._fail_one(db, seller, fixed[0], buyer="demo-weesstock-buyer-2", index=0) is None


class TestBackdatingIntegrity:
    """The seeder's one deviation from the pure service path, and its biggest risk."""

    def test_receipt_hash_still_verifies_after_backdating(self, shop_with_catalogue):
        """THE critical test. Backdating is only sound because ``_receipt_hash`` canonicalizes the
        money fields and the chain tip — never a timestamp. Recompute the hash from the stored row
        and compare. If anyone adds a timestamp to that canonical string, this fails loudly instead
        of leaving the dev DB full of receipts that no longer verify."""
        db, seller = shop_with_catalogue
        fixed, _ = seeder._sellable(db, str(seller.id))
        order = seeder._settle_one(db, seller, fixed[0], buyer="demo-weesstock-buyer-0", index=0)
        seeder._backdate(db, str(order.id), NOW - timedelta(days=40))
        db.commit()

        receipt = db.query(Receipt).filter(Receipt.order_id == order.id).one()
        canonical = "|".join([
            receipt.order_id, receipt.buyer_uuid, receipt.seller_id, receipt.listing_id,
            str(receipt.gross_cents), str(receipt.commission_cents),
            str(receipt.net_to_seller_cents), receipt.rail_ref or "", receipt.chain_tip_hash,
        ])
        assert receipt.receipt_hash == hashlib.sha256(canonical.encode()).hexdigest()

    def test_order_event_chain_still_verifies_after_backdating(self, shop_with_catalogue):
        """The §7 append-only ledger must remain tamper-evident. Walk the whole chain and
        recompute every link from the stored rows."""
        db, seller = shop_with_catalogue
        fixed, _ = seeder._sellable(db, str(seller.id))
        order = seeder._settle_one(db, seller, fixed[0], buyer="demo-weesstock-buyer-0", index=0)
        seeder._backdate(db, str(order.id), NOW - timedelta(days=40))
        db.commit()

        events = (db.query(OrderEvent)
                  .filter(OrderEvent.order_id == order.id)
                  .order_by(OrderEvent.seq).all())
        assert len(events) >= 4                      # open, lock, settle_record, settle_ok
        prev = None
        for e in events:
            canonical = "|".join([
                e.order_id, str(e.seq), e.event_type, e.actor_uuid or "",
                "" if e.amount_cents is None else str(e.amount_cents), prev or "",
            ])
            assert e.row_hash == hashlib.sha256(canonical.encode()).hexdigest(), f"seq {e.seq}"
            assert e.prev_hash == prev
            prev = e.row_hash

    def test_both_timestamp_columns_move_together(self, shop_with_catalogue):
        """The scorer windows receipts and orders INDEPENDENTLY. Moving only one produces a shop
        with revenue but no orders (or vice versa) — impossible in reality, and it would make the
        card misreport fulfilment."""
        db, seller = shop_with_catalogue
        fixed, _ = seeder._sellable(db, str(seller.id))
        order = seeder._settle_one(db, seller, fixed[0], buyer="demo-weesstock-buyer-0", index=0)
        when = NOW - timedelta(days=37)
        seeder._backdate(db, str(order.id), when)
        db.commit()

        row = db.query(Order).filter(Order.id == order.id).one()
        receipt = db.query(Receipt).filter(Receipt.order_id == order.id).one()

        def _aware(dt):
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)

        assert abs((_aware(row.created_at) - when).total_seconds()) < 1
        assert abs((_aware(receipt.issued_at) - when).total_seconds()) < 1


class TestSpread:
    def test_seeds_a_scored_profile(self, shop_with_catalogue):
        """End-to-end: after seeding, the scorer must actually return a composite. This is the
        whole point — the dev DB had zero receipts, so the scored path was unreachable."""
        db, seller = shop_with_catalogue
        seeder.seed(db, now=NOW)

        profile = credit_score.compute_credit_profile(db, seller, now=NOW)
        assert profile.score is not None, profile.missing_for_score
        assert profile.signals.settled_orders >= MIN_ORDERS_FOR_SCORE
        assert profile.signals.revenue_cents > 0

    def test_sales_never_predate_the_seller(self, db_session, dev):
        """A receipt older than the shop is not something a lender could ever see. Tenure here is
        deliberately shorter than the 90-day revenue window, which is the live situation."""
        s = _seller(db_session, "young", "Young Shop", tenure_days=35)
        _listing(db_session, _shop(db_session, s), price=90_000)
        db_session.commit()
        seeder.seed(db_session, now=NOW)

        created = NOW - timedelta(days=35)
        for (issued,) in db_session.query(Receipt.issued_at).all():
            issued = issued if issued.tzinfo else issued.replace(tzinfo=timezone.utc)
            assert issued >= created, "receipt predates the seller"
            assert issued <= NOW

    def test_all_sales_land_inside_the_revenue_window(self, shop_with_catalogue):
        """A sale outside the 90-day window contributes nothing, so it would be wasted rows — and
        a sale ON the boundary would silently age out overnight and change the card by itself."""
        db, seller = shop_with_catalogue
        seeder.seed(db, now=NOW)

        window_start = NOW - timedelta(days=credit_score.REVENUE_WINDOW_DAYS)
        for (issued,) in db.query(Receipt.issued_at).all():
            issued = issued if issued.tzinfo else issued.replace(tzinfo=timezone.utc)
            assert issued > window_start

    def test_repeat_rate_is_between_the_extremes(self, shop_with_catalogue):
        """Repeat-buyer rate is a scored component (W_REPEAT = 0.15), and it must carry information.

        Regression, measured against the real dev DB: the original seeder cycled ONE pool of 8
        buyers, and since every tier settles >= 13 sales, all 8 necessarily bought more than once —
        so repeat_rate was exactly 1.00 for all seven scored sellers. A component that reads the
        same maximum for everybody differentiates nothing, and no real shop has 100% returning
        buyers. Both extremes are therefore asserted away, not just the zero one.
        """
        db, seller = shop_with_catalogue
        seeder.seed(db, now=NOW)

        signals = credit_score.compute_credit_profile(db, seller, now=NOW).signals
        assert signals.repeat_buyers > 0, "no repeat custom at all makes the term dead"
        assert signals.repeat_rate < 1.0, "every buyer repeating pins the term at its maximum"
        assert signals.unique_buyers > signals.repeat_buyers

    def test_buyer_mix_matches_the_tier(self, shop_with_catalogue):
        """The repeat rate should be the tier's chosen number, not an artefact of the sale count —
        that is the whole point of splitting regulars from one-timers."""
        db, seller = shop_with_catalogue
        seeder.seed(db, now=NOW)

        tier = seeder.TIERS[0]
        signals = credit_score.compute_credit_profile(db, seller, now=NOW).signals
        assert signals.unique_buyers == tier.regulars + tier.one_timers
        assert signals.repeat_buyers == tier.regulars

    def test_shops_do_not_share_customers(self, db_session, dev):
        """Buyer ids are namespaced per seller. Sharing would leave the repeat term correct (the
        scorer groups per seller) but would make the platform look like N shops serving one small
        group of people."""
        for n in range(2):
            s = _seller(db_session, f"s{n}", f"Shop {n}", tenure_days=60)
            sh = _shop(db_session, s, f"Shop {n}")
            _listing(db_session, sh, price=300_000, title=f"{n}-a")
            _listing(db_session, sh, price=150_000, mode="bargain", title=f"{n}-b")
        db_session.commit()
        seeder.seed(db_session, now=NOW)

        by_seller = {}
        for order in db_session.query(Order).all():
            by_seller.setdefault(order.seller_id, set()).add(order.buyer_uuid)

        # 2 real shops + the 2 thin-file demo sellers seed() always creates.
        assert len(by_seller) == 4
        seen = set()
        for buyers in by_seller.values():
            assert not (seen & buyers), "shops must not draw from the same customers"
            seen |= buyers

    def test_fulfilment_is_not_perfect(self, shop_with_catalogue):
        """A demo where every shop has 100% fulfilment never exercises the failed-order path, and
        makes the fulfilment bar look decorative."""
        db, seller = shop_with_catalogue
        seeder.seed(db, now=NOW)

        profile = credit_score.compute_credit_profile(db, seller, now=NOW)
        assert profile.signals.failed_orders > 0
        assert 0.0 < profile.signals.fulfilment_rate < 1.0

    def test_tiers_produce_genuinely_different_profiles(self, db_session, dev):
        """The spread has to actually spread. Two shops with identical catalogues but different
        tiers must score differently, or the ladder is decorative."""
        subs = []
        for n in range(2):
            s = _seller(db_session, f"s{n}", f"Shop {n}", tenure_days=60)
            sh = _shop(db_session, s, f"S{n}")
            # Identical catalogues, so ONLY the tier can explain a score difference.
            for i in range(3 - n):        # different sellable counts drive the tier ordering
                _listing(db_session, sh, price=150_000, title=f"{n}-{i}")
            _listing(db_session, sh, price=150_000, mode="bargain", title=f"{n}-b")
            subs.append(s)
        db_session.commit()
        seeder.seed(db_session, now=NOW)

        scores = [credit_score.compute_credit_profile(db_session, s, now=NOW).score for s in subs]
        assert all(x is not None for x in scores), scores
        assert scores[0] > scores[1], "the stronger tier must score higher"


class TestThinFileCases:
    def test_both_cold_start_branches_are_seeded(self, db_session, dev):
        """The card's growth prompt is built server-side from orders_needed / days_needed, so each
        gate needs its own failing case to be inspectable at all."""
        seeder._seed_thin(db_session, now=NOW)

        thin = (db_session.query(Seller)
                .filter(Seller.user_uuid.like(f"{seeder.THIN_PREFIX}%"))
                .order_by(Seller.user_uuid).all())
        assert len(thin) == 2

        a, b = [credit_score.compute_credit_profile(db_session, s, now=NOW) for s in thin]
        assert a.score is None and b.score is None

        # A fails ONLY the order gate; B fails ONLY the tenure gate.
        assert a.orders_needed > 0, "thin A must be short on orders"
        assert a.days_needed == 0, "thin A's tenure must already clear the gate"
        assert b.days_needed > 0, "thin B must be short on days"
        assert b.orders_needed == 0, "thin B must already have enough orders"

    def test_thin_sellers_have_real_receipts(self, db_session, dev):
        """Thin-file must mean "not enough evidence", not "no data" — the components still render,
        so the sales have to be real settled rows."""
        seeder._seed_thin(db_session, now=NOW)
        thin_ids = [str(s.id) for s in db_session.query(Seller)
                    .filter(Seller.user_uuid.like(f"{seeder.THIN_PREFIX}%")).all()]
        assert db_session.query(Receipt).filter(Receipt.seller_id.in_(thin_ids)).count() > 0

    def test_thin_tenure_is_reasserted_on_every_run(self, db_session, dev):
        """Tenure is measured from NOW, so a fixed created_at would drift past the gate as days
        pass and the thin-file demo would silently start scoring. Re-running must re-pin it."""
        seeder._seed_thin(db_session, now=NOW)
        later = NOW + timedelta(days=60)
        seeder._seed_thin(db_session, now=later)

        b = (db_session.query(Seller)
             .filter(Seller.user_uuid == f"{seeder.THIN_PREFIX}b").one())
        profile = credit_score.compute_credit_profile(db_session, b, now=later)
        assert profile.score is None, "the tenure-gate case must still be thin 60 days later"
        assert profile.signals.tenure_days < MIN_TENURE_DAYS


class TestIdempotency:
    def test_second_run_does_not_double_revenue(self, shop_with_catalogue):
        """The property that makes this safe to re-run against a live dev DB. Every transition
        carries a deterministic idempotency key, so a re-run replays rather than re-sells."""
        db, seller = shop_with_catalogue
        seeder.seed(db, now=NOW)
        first = credit_score.compute_credit_profile(db, seller, now=NOW)
        receipts_after_first = db.query(Receipt).count()

        seeder.seed(db, now=NOW)
        second = credit_score.compute_credit_profile(db, seller, now=NOW)

        assert db.query(Receipt).count() == receipts_after_first
        assert second.signals.revenue_cents == first.signals.revenue_cents
        assert second.signals.settled_orders == first.signals.settled_orders
        assert second.score == first.score

    def test_reviews_are_not_duplicated(self, shop_with_catalogue):
        """UNIQUE(order_id) makes a re-review a ConflictError; the seeder must treat that as
        "already done" rather than an error, and must not leave the session dirty."""
        db, seller = shop_with_catalogue
        seeder.seed(db, now=NOW)
        before = db.query(Review).count()
        seeder.seed(db, now=NOW)
        assert db.query(Review).count() == before
        assert before > 0


class TestPurge:
    def test_purge_removes_everything_it_created(self, shop_with_catalogue):
        db, seller = shop_with_catalogue
        seeder.seed(db, now=NOW)
        assert db.query(Receipt).count() > 0

        seeder.purge(db)

        assert db.query(Receipt).count() == 0
        assert db.query(Order).filter(
            Order.buyer_uuid.like(f"{seeder.BUYER_PREFIX}%")).count() == 0
        assert db.query(Seller).filter(
            Seller.user_uuid.like(f"{seeder.THIN_PREFIX}%")).count() == 0

    def test_purge_leaves_a_real_buyers_history_alone(self, shop_with_catalogue):
        """Scoped strictly to the demo prefixes: a real buyer's settled order, its receipt and its
        review must survive. This is the guard that makes --purge safe to run on a dev DB that also
        holds hand-made test data."""
        db, seller = shop_with_catalogue
        fixed, _ = seeder._sellable(db, str(seller.id))
        real = seeder._settle_one(db, seller, fixed[0], buyer="real-human-buyer", index=999)
        assert real is not None
        seeder._review_one(db, real, 5)
        db.commit()

        seeder.seed(db, now=NOW)
        seeder.purge(db)

        assert db.query(Order).filter(Order.id == real.id).count() == 1
        assert db.query(Receipt).filter(Receipt.order_id == real.id).count() == 1
        assert db.query(Review).filter(Review.order_id == real.id).count() == 1
        assert db.query(OrderEvent).filter(OrderEvent.order_id == real.id).count() >= 4

    def test_purge_leaves_real_sellers_and_listings_alone(self, shop_with_catalogue):
        """Only the thin DEMO sellers' shops/listings are removable. Deleting a real seller's
        catalogue would be catastrophic and silent."""
        db, seller = shop_with_catalogue
        seeder.seed(db, now=NOW)
        seeder.purge(db)

        assert db.query(Seller).filter(Seller.id == seller.id).count() == 1
        assert db.query(Listing).filter(Listing.seller_id == seller.id).count() == 3
        assert db.query(Shop).filter(Shop.seller_id == seller.id).count() == 1

    def test_purge_is_idempotent(self, shop_with_catalogue):
        db, _ = shop_with_catalogue
        seeder.seed(db, now=NOW)
        seeder.purge(db)
        assert seeder.purge(db)["orders"] == 0


class TestDryRun:
    def test_dry_run_writes_nothing(self, shop_with_catalogue):
        """Rule 9's corollary: you must be able to see the plan before it touches money rows."""
        db, _ = shop_with_catalogue
        plan = seeder.seed(db, now=NOW, dry_run=True)

        assert plan and all("tier" in row for row in plan)
        assert db.query(Order).count() == 0
        assert db.query(Receipt).count() == 0

    def test_dry_run_reports_skipped_sellers(self, db_session, dev):
        """A skip must read as a decision, not an oversight — Eva Mokaya is the live example."""
        s = _seller(db_session, "s-eva", "Eva")
        _listing(db_session, _shop(db_session, s, "My timeline"), price=0, kind="post")
        db_session.commit()

        plan = seeder.seed(db_session, now=NOW, dry_run=True)
        assert any("SKIPPED" in row["tier"] for row in plan)

    def test_dry_run_reports_real_tenure_for_every_row(self, db_session, dev):
        """Regression: the plan omitted ``tenure_days`` and main() printed a ``.get(..., 0)``
        default, so a dry run reported ``tenure=0d`` for every seller — which reads as "nothing
        can score", since tenure is the cold-start gate. The plan must carry the SAME tenure the
        real run computes, including for skipped sellers.
        """
        traded = _seller(db_session, "s-traded", "Traded", tenure_days=45)
        _listing(db_session, _shop(db_session, traded, "Traded Shop"), price=500_000)
        skipped = _seller(db_session, "s-skipped", "Skipped", tenure_days=12)
        _listing(db_session, _shop(db_session, skipped, "My timeline"), price=0, kind="post")
        db_session.commit()

        by_name = {r["seller"]: r for r in seeder.seed(db_session, now=NOW, dry_run=True)}

        assert by_name["Traded"]["tenure_days"] == 45
        assert by_name["Skipped"]["tenure_days"] == 12, "skipped rows need real tenure too"
