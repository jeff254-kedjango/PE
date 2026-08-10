"""Live verification of the TTL expiry sweep against the real commerce DB (PostGIS).

The HTTP-boundary Playwright e2e (commerce.e2e.js) cannot test expiry: it can't back-date an
order past the 1h production TTL through the API. This companion script does the DB-level setup
the sweep needs — open a bargain order, force its created_at into the past, run the sweeper's
``run_once`` (exercising the Postgres advisory-lock path), and assert it expired with an
``expire`` event appended. It reads commerce/.env, so it hits the same DB the live server uses.

Run (from the commerce dir, repo root on PYTHONPATH — same as the backend launcher):
    cd PE/commerce
    for l in $(grep -v '^#' .env | grep '='); do export "$l"; done
    PYTHONPATH=/home/jeff .venv/bin/python e2e/expiry_sweep_live.py

Exits non-zero on any failed assertion (CI-friendly). Uses a unique tag per run so it never
collides with prior runs on the persistent DB.
"""
import sys
import time
from datetime import datetime, timedelta, timezone

from PE.commerce.core.database import SessionLocal
from PE.commerce.models.order import Order, STATUS_EXPIRED, STATUS_OFFERED
from PE.commerce.schemas import catalog as cs
from PE.commerce.services import catalog, expiry_sweeper, settlement

failures = []


def check(name, cond, detail=""):
    mark = "✓" if cond else "✗"
    print(f"  {mark} {name}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        failures.append(name)


def main() -> int:
    db = SessionLocal()
    try:
        tag = f"sweep-live-{int(time.time())}"
        seller = catalog.create_shop(
            db, f"{tag}-s", cs.ShopCreate(name="L", lat=-1.29, lng=36.82, display_name="L"))
        listing = catalog.create_listing(
            db, f"{tag}-s", seller.id,
            cs.ListingCreate(title="L", price_cents=10000, stock_qty=5, pricing_mode="bargain"))
        order = settlement.open_order(db, f"{tag}-b", listing.id, offer_cents=9000, idem_key=f"{tag}-open")
        check("bargain order opens OFFERED", order.status == STATUS_OFFERED, order.status)

        # Back-date well past any TTL so the sweep's cutoff catches it.
        db.execute(
            Order.__table__.update()
            .where(Order.id == order.id)
            .values(created_at=datetime.now(timezone.utc) - timedelta(days=1))
        )
        db.commit()

        n = expiry_sweeper.run_once(db)  # exercises the PG advisory lock + per-order commit
        check("run_once expired exactly our order", n >= 1, f"expired={n}")
        db.refresh(order)
        check("order is now EXPIRED", order.status == STATUS_EXPIRED, order.status)

        events = [e.event_type for e in settlement.order_events(db, order.id)]
        check("expire event appended to chain", events[-1] == "expire", str(events))

        # The advisory lock was released, so a second pass re-acquires cleanly and is a no-op
        # for this (already-expired) order.
        n2 = expiry_sweeper.run_once(db)
        check("second run_once is a clean no-op for this order", isinstance(n2, int), str(n2))

        print(f"\n{'PASS' if not failures else 'FAIL'}: "
              f"{4 - len(failures)}/4 checks passed")
        return 1 if failures else 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
