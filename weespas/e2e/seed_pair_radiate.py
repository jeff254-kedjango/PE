#!/usr/bin/env python3
"""Dev-only, run-scoped seed/teardown for the §8.1b pair-radiate live e2e.

The pair-radiate e2e proves the whole realtime slice end-to-end on the REAL stack:
  * BUYER half — a buyer opens a shop pin → POST /insar/contact returns the buyer's OWN footprints
    in the AOI, which the InSAR SPA glows fuchsia locally. To exercise that, the buyer must actually
    OWN a linked footprint, which follows the shipped spine ``User.agent_id → Property.agent_id →
    BuildingLink``. So this seeds: an Agent, a buyer User carrying that agent_id, a throwaway
    Property owned by the agent, and a BuildingLink on the buyer's OWN building.
  * SELLER half — the shop's owning seller, viewing the map on their OWN SSE channel, sees an
    anonymized pulse on their shop's footprint. That needs only a BuildingLink on the SHOP building
    (so the aggregator paints the pin AND shop_footprint_exists() passes) — the commerce Shop +
    Seller.user_uuid half is seeded separately by the e2e via the commerce HTTP API and torn down by
    commerce/e2e/cleanup_run.py. This helper owns ONLY the weespas rows.

Everything is tagged with the run id so teardown removes EXACTLY what a run made:
  - the buyer User id / Agent id / Property id embed the run id
  - the Property title  LIKE '%<run>%'
so a caller bug can never widen the delete to real data (guards mirror seed_shop_on_map.py).

NOT an HTTP endpoint on purpose (writes across the schema; must never be reachable in prod). Runs
out-of-band against the same DB the server uses (core.database.SessionLocal).

Usage (from PE/weespas, venv active, PYTHONPATH=/home/jeff):
    python e2e/seed_pair_radiate.py seed  <run> <aoi> <shop_building_id> <buyer_building_id> \
        <buyer_uuid> <shop_property_uuid>
    python e2e/seed_pair_radiate.py clean <run>
`seed` prints nothing on success (exit 0); the caller already knows the ids it passed in. `clean`
prints a summary. Idempotent: a re-run replaces its own rows.
"""
from __future__ import annotations

import sys
from decimal import Decimal

from sqlalchemy import text

from PE.weespas.core.database import SessionLocal
from PE.weespas.models.insar_link import BuildingLink
from PE.weespas.models.property import Agent, Property, PropertyListingType
from PE.weespas.models.user import User, UserRole

# A run id is a short script-generated tag like "pr-1783003130752". Guard hard against an empty or
# wildcard argument so a caller bug can never turn teardown into "delete everything" (the LIKE below
# would match every row). Mirrors seed_shop_on_map.py / cleanup_run.py exactly.
_MIN_RUN_LEN = 6
_TITLE_PREFIX = "E2E pair-radiate"


def _reject_unsafe_run(run: str) -> None:
    if len(run) < _MIN_RUN_LEN or "%" in run or "_" in run.replace("-", ""):
        print(f"refusing unsafe run id: {run!r}", file=sys.stderr)
        raise SystemExit(2)


def _seed(
    run: str,
    aoi: str,
    shop_building_id: int,
    buyer_building_id: int,
    buyer_uuid: str,
    shop_property_uuid: str,
) -> int:
    _reject_unsafe_run(run)
    for label, val in (("buyer_uuid", buyer_uuid), ("shop_property_uuid", shop_property_uuid)):
        if run not in val:
            # These embed the run id so `clean` finds them; reject anything that doesn't.
            print(f"{label} {val!r} must embed run id {run!r}", file=sys.stderr)
            return 2

    db = SessionLocal()
    try:
        cat_id = db.execute(text("select id from property_categories limit 1")).scalar()
        if not cat_id:
            print("no property_categories row to anchor the throwaway Property", file=sys.stderr)
            return 1

        # Idempotent: clear any prior rows for THIS run first (a re-run must not collide on PKs or
        # the BuildingLink unique (listing_id, insar_building_id)).
        _clean(run, _db=db, _commit=False)

        # The buyer's Agent (ownership pivot) + buyer User carrying agent_id (so the resolver's
        # User.agent_id → Property.agent_id join matches). Ids embed the run id for teardown.
        agent_id = f"agent-{run}"
        # Phone/email must be unique; derive short unique-ish values from the run id.
        tag = run[-12:]
        db.add(Agent(id=agent_id, agent_name=f"{_TITLE_PREFIX} {run}", agent_phone_number=f"+254{tag[-9:]:0>9}"))
        db.flush()
        db.add(User(
            id=buyer_uuid, name=f"{_TITLE_PREFIX} buyer {run}",
            email=f"{buyer_uuid}@e2e.local", phone=f"+255{tag[-9:]:0>9}",
            hashed_password="x", role=UserRole.AGENT, agent_id=agent_id, is_active=True,
        ))

        # The buyer's OWN property + a BuildingLink on the BUYER's building (the buyer-half glow).
        buyer_prop_id = f"buyerprop-{run}"
        db.add(Property(
            id=buyer_prop_id, title=f"{_TITLE_PREFIX} buyer-listing {run}",
            price=Decimal("1000000.00"), listing_type=PropertyListingType.SALE,
            category_id=cat_id, agent_id=agent_id, is_active=False,
        ))
        db.flush()
        db.add(BuildingLink(
            listing_id=buyer_prop_id, aoi_code=aoi, insar_building_id=buyer_building_id,
            match_method="pip", match_confidence=1.0,
        ))

        # The SHOP's property + BuildingLink on the SHOP building (so the aggregator paints the pin
        # and shop_footprint_exists() passes). It is owned by a DISTINCT throwaway agent — NOT the
        # buyer's — so the buyer resolves to ONLY their own footprint, never the shop building. That
        # keeps the buyer-half assertion faithful (the shop is the OTHER party, not the buyer's). The
        # weespas owner is otherwise irrelevant to the seller half: the commerce Seller.user_uuid
        # (seeded via the API) is what routes the pulse.
        shop_agent_id = f"shopagent-{run}"
        db.add(Agent(id=shop_agent_id, agent_name=f"{_TITLE_PREFIX} shop {run}",
                     agent_phone_number=f"+254{tag[-8:]:0>8}9"))
        db.flush()
        db.add(Property(
            id=shop_property_uuid, title=f"{_TITLE_PREFIX} shop-listing {run}",
            price=Decimal("1000000.00"), listing_type=PropertyListingType.SALE,
            category_id=cat_id, agent_id=shop_agent_id, is_active=False,
        ))
        db.flush()
        db.add(BuildingLink(
            listing_id=shop_property_uuid, aoi_code=aoi, insar_building_id=shop_building_id,
            match_method="pip", match_confidence=1.0,
        ))

        db.commit()
        return 0
    except Exception as exc:  # noqa: BLE001 — report and fail loudly, never leave a half seed
        db.rollback()
        print(f"seed {run} FAILED, rolled back: {exc}", file=sys.stderr)
        return 1
    finally:
        db.close()


def _clean(run: str, *, _db=None, _commit: bool = True) -> int:
    """Delete every weespas row this run seeded, FK-safe (BuildingLink → Property → User → Agent).
    Scoped to ids/titles embedding the run id, so a shared footprint's genuine rows are never
    touched. Reused by `_seed` (with _commit=False) for idempotent re-seeding."""
    _reject_unsafe_run(run)
    db = _db or SessionLocal()
    try:
        # Properties this run seeded (buyer-listing + shop-listing) carry the run id in their title.
        pids = [r[0] for r in db.execute(text(
            "select id from properties where title like :mid"
        ), {"mid": f"%{run}%"}).fetchall()]
        total = 0
        # BuildingLinks hang off those properties (FK to properties.id) — delete leaves-first.
        for pid in pids:
            total += db.execute(text(
                "delete from building_link where listing_id = :pid"
            ), {"pid": pid}).rowcount
        for pid in pids:
            total += db.execute(text("delete from properties where id = :pid"), {"pid": pid}).rowcount
        # The buyer User (agent_id FK is ON DELETE SET NULL, so delete the user before the agent).
        total += db.execute(text(
            "delete from users where name like :mid"
        ), {"mid": f"%{run}%"}).rowcount
        total += db.execute(text(
            "delete from agents where agent_name like :mid"
        ), {"mid": f"%{run}%"}).rowcount
        if _commit:
            db.commit()
            print(f"clean {run}: removed {total} weespas rows across {len(pids)} seeded properties")
        return 0
    except Exception as exc:  # noqa: BLE001
        if _commit:
            db.rollback()
            print(f"clean {run} FAILED, rolled back: {exc}", file=sys.stderr)
        raise
    finally:
        if _db is None:
            db.close()


def main() -> int:
    argv = sys.argv[1:]
    if not argv:
        print(__doc__, file=sys.stderr)
        return 2
    cmd = argv[0]
    if cmd == "seed":
        if len(argv) != 7:
            print("usage: seed_pair_radiate.py seed <run> <aoi> <shop_building_id> "
                  "<buyer_building_id> <buyer_uuid> <shop_property_uuid>", file=sys.stderr)
            return 2
        run, aoi = argv[1], argv[2]
        try:
            shop_bid = int(argv[3])
            buyer_bid = int(argv[4])
        except ValueError:
            print("building ids must be integers", file=sys.stderr)
            return 2
        return _seed(run, aoi, shop_bid, buyer_bid, argv[5], argv[6])
    if cmd == "clean":
        if len(argv) != 2:
            print("usage: seed_pair_radiate.py clean <run>", file=sys.stderr)
            return 2
        return _clean(argv[1])
    print(f"unknown command {cmd!r} (want 'seed' or 'clean')", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
