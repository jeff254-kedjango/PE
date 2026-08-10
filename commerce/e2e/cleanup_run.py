#!/usr/bin/env python3
"""Dev-only, run-scoped teardown for the live Playwright e2e.

The write-path e2e create REAL shops/listings/boosts in the live commerce DB to exercise the
seller flow end-to-end. Without teardown they accumulate and pollute the buyer-facing feed with
fake products (exactly what "seeded data everywhere" turned out to be). This helper deletes only
the rows a single e2e run created, keyed by that run's unique id, in FK-safe order.

It is NOT an HTTP endpoint on purpose: cleanup touches every table and must never be reachable in
production. It runs out-of-band against the same DB the server uses (core.database.SessionLocal),
invoked by each e2e in a `finally` block via `node -> spawn(python)`.

A run created a row iff one of these carries its run id:
  - the synthetic seller's user_uuid  (e.g. "trend-1783...-farseller")  -> LIKE '<run>%'
  - the shop name                     (e.g. "EDP Shop edp-1783...")     -> LIKE '%<run>%'
  - the listing title                 (e.g. "Nationwide Loaf edp-...")  -> LIKE '%<run>%'
The admin-logged scripts (edp/fe2a) reuse the real admin seller, so we must NOT key on seller alone
— shop-name / title matching catches those without ever touching the seller's real identity or its
other (genuine) shops.

Usage (from PE/commerce, venv active, PYTHONPATH=/home/jeff):
    python e2e/cleanup_run.py <run-id>
Exit 0 on success (prints a one-line summary); non-zero on bad input or DB error. Safe to call
twice (idempotent) and safe when the run created nothing (deletes 0).
"""
import sys

from PE.commerce.core.database import SessionLocal
from sqlalchemy import text

# A run id is a short, script-generated tag like "edp-1783003130752". Guard hard against an empty
# or wildcard argument so a bug in a caller can never turn this into a "delete everything" — the
# LIKE patterns below would match every row if `run` were '' or '%'.
_MIN_RUN_LEN = 6


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: cleanup_run.py <run-id>", file=sys.stderr)
        return 2
    run = sys.argv[1].strip()
    if len(run) < _MIN_RUN_LEN or "%" in run or "_" in run.replace("-", ""):
        # (underscore is a single-char LIKE wildcard; our run ids only use letters/digits/hyphen)
        print(f"refusing unsafe run id: {run!r}", file=sys.stderr)
        return 2

    db = SessionLocal()
    try:
        # Bind the run id as a parameter everywhere; build the two LIKE forms once.
        params = {"pre": f"{run}%", "mid": f"%{run}%"}

        # Residue id sets for THIS run, materialised as temp tables so every delete below is scoped
        # identically and the planner sees stable sets.
        db.execute(text(
            "create temp table _r_shops as "
            "select sh.id from shops sh join sellers s on s.id = sh.seller_id "
            "where sh.name like :mid or s.user_uuid like :pre"
        ), params)
        db.execute(text(
            "create temp table _r_listings as "
            "select l.id from listings l join sellers s on s.id = l.seller_id "
            "where l.shop_id in (select id from _r_shops) "
            "   or l.title like :mid or s.user_uuid like :pre"
        ), params)
        # Only SYNTHETIC sellers (run-tagged uuid) are deleted — never the real admin seller that
        # edp/fe2a log in as. Its run-tagged shops/listings are removed above; its identity stays.
        db.execute(text(
            "create temp table _r_sellers as "
            "select id from sellers where user_uuid like :pre"
        ), params)

        rl = "(select id from _r_listings)"
        rs = "(select id from _r_shops)"
        rse = "(select id from _r_sellers)"
        rord = f"(select id from orders where listing_id in {rl})"

        # FK-safe order: leaves -> ... -> listings -> shops -> synthetic sellers.
        steps = [
            ("comment_likes",      f"delete from comment_likes where comment_id in (select id from listing_comments where listing_id in {rl})"),
            ("listing_comments",   f"delete from listing_comments where listing_id in {rl}"),
            ("listing_inquiries",  f"delete from listing_inquiries where listing_id in {rl}"),
            ("saved_listings",     f"delete from saved_listings where listing_id in {rl}"),
            ("shop_subscriptions", f"delete from shop_subscriptions where shop_id in {rs}"),
            ("order_events",       f"delete from order_events where order_id in {rord}"),
            ("receipts",           f"delete from receipts where listing_id in {rl} or order_id in {rord}"),
            ("reviews",            f"delete from reviews where listing_id in {rl} or order_id in {rord}"),
            ("orders",             f"delete from orders where listing_id in {rl}"),
            # Boosts: by residue listing (covers grants held by the real admin seller targeting a
            # run listing) OR by synthetic seller.
            ("boost_grants",       f"delete from boost_grants where (target_type='listing' and target_id in {rl}) or seller_id in {rse}"),
            ("boost_allowances",   f"delete from boost_allowances where seller_id in {rse}"),
            ("listings",           f"delete from listings where id in {rl}"),
            # Per-shop sponsored-cap overrides (§8.3) FK shops(id) with no cascade, so they must go
            # before the shops delete or the whole teardown rolls back on a FK violation.
            ("sponsored_cap_overrides", f"delete from shop_sponsored_cap_overrides where shop_id in {rs}"),
            ("shops",              f"delete from shops where id in {rs}"),
            ("sellers",            f"delete from sellers where id in {rse}"),
        ]
        counts = {}
        for name, q in steps:
            counts[name] = db.execute(text(q)).rowcount
        db.commit()
        total = sum(counts.values())
        summary = ", ".join(f"{k}={v}" for k, v in counts.items() if v)
        print(f"cleanup {run}: removed {total} rows" + (f" ({summary})" if summary else " (nothing to clean)"))
        return 0
    except Exception as exc:  # noqa: BLE001 — report and fail loudly, never leave a half delete
        db.rollback()
        print(f"cleanup {run} FAILED, rolled back: {exc}", file=sys.stderr)
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
