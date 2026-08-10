#!/usr/bin/env python3
"""Dev-only, run-scoped seed/teardown for the §8.1a shops-on-the-InSAR-map live e2e.

The pin-render e2e needs a REAL BuildingLink so the weespas aggregator (GET /insar/shops/near)
returns a pin the InSAR map can paint. A BuildingLink FK-references properties(id), so this also
seeds a throwaway Property to hang the link off — we never touch the 177 genuine listings. The
commerce SHOP half is seeded separately by the e2e via the commerce HTTP API (so PostGIS geog is
built correctly) and torn down by the existing commerce cleanup_run.py; this helper owns ONLY the
weespas rows: Property, BuildingLink, and (for the confirmed variant) StructuralFlag.

Everything this creates is tagged with the run id so teardown removes EXACTLY what a run made:
  - the Property id      == the caller-supplied property_uuid (which embeds the run id)
  - the Property title    LIKE '%<run>%'
so a bug in a caller can never widen the delete to real data (the guards below reject an empty or
wildcard run id, exactly like commerce/e2e/cleanup_run.py).

It is NOT an HTTP endpoint on purpose (it writes across the schema and must never be reachable in
prod). It runs out-of-band against the same DB the server uses (core.database.SessionLocal).

Usage (from PE/weespas, venv active, PYTHONPATH=/home/jeff):
    python e2e/seed_shop_on_map.py seed  <run> <aoi> <building_id> <property_uuid> [--confirmed]
    python e2e/seed_shop_on_map.py clean <run>
`seed` prints the property_uuid it created (the stitch key the commerce shop must reuse). Exit 0 on
success; non-zero on bad input or DB error. Idempotent: a re-run replaces its own rows.
"""
from __future__ import annotations

import sys
from decimal import Decimal

from sqlalchemy import text

from PE.weespas.core.database import SessionLocal
from PE.weespas.models.insar_link import BuildingLink, StructuralFlag, FLAG_UNSAFE
from PE.weespas.models.property import Property, PropertyListingType

# A run id is a short, script-generated tag like "som-1783003130752". Guard hard against an empty
# or wildcard argument so a caller bug can never turn teardown into "delete everything" (the LIKE
# below would match every Property if `run` were '' or '%'). Mirrors cleanup_run.py exactly.
_MIN_RUN_LEN = 6

# A first, real category id is resolved at seed time (the Property.category_id FK must point at a
# genuine PropertyCategory). We only READ it — never create/delete a category.
_TITLE_PREFIX = "E2E shops-on-map"


def _reject_unsafe_run(run: str) -> None:
    if len(run) < _MIN_RUN_LEN or "%" in run or "_" in run.replace("-", ""):
        # (underscore is a single-char LIKE wildcard; our run ids only use letters/digits/hyphen)
        print(f"refusing unsafe run id: {run!r}", file=sys.stderr)
        raise SystemExit(2)


def _seed(run: str, aoi: str, building_id: int, property_uuid: str, confirmed: bool) -> int:
    _reject_unsafe_run(run)
    if run not in property_uuid:
        # The property_uuid IS the teardown key; if it doesn't embed the run id, clean would miss it.
        print(f"property_uuid {property_uuid!r} must embed run id {run!r}", file=sys.stderr)
        return 2

    db = SessionLocal()
    try:
        cat_id = db.execute(text("select id from property_categories limit 1")).scalar()
        if not cat_id:
            print("no property_categories row to anchor the throwaway Property", file=sys.stderr)
            return 1

        # Idempotent: clear any prior rows for THIS property_uuid first (a re-run must not collide
        # on the BuildingLink unique (listing_id, insar_building_id) or the Property PK).
        _clean_property(db, property_uuid)

        prop = Property(
            id=property_uuid,
            title=f"{_TITLE_PREFIX} {run}",
            price=Decimal("1000000.00"),
            # Pass the enum MEMBER (not a raw string): the PG enum stores member names, and
            # SQLAlchemy maps the Python enum to the right label. A bare "sale" is rejected.
            listing_type=PropertyListingType.SALE,
            category_id=cat_id,
            verification_status="not_monitored",
            is_active=False,  # never surfaces in a real listing feed — it's e2e scaffolding
        )
        db.add(prop)
        db.flush()

        db.add(BuildingLink(
            listing_id=property_uuid,
            aoi_code=aoi,
            insar_building_id=building_id,
            match_method="pip",
            match_confidence=1.0,
        ))

        if confirmed:
            # A recorded structural assessment on this footprint ⇒ the aggregator marks the pin
            # confirmed=true (provenance, not a safety claim). FLAG_UNSAFE is the strongest "assessed".
            db.add(StructuralFlag(
                aoi_code=aoi,
                insar_building_id=building_id,
                state=FLAG_UNSAFE,
                source="e2e",
            ))
        db.commit()
        print(property_uuid)
        return 0
    except Exception as exc:  # noqa: BLE001 — report and fail loudly, never leave a half seed
        db.rollback()
        print(f"seed {run} FAILED, rolled back: {exc}", file=sys.stderr)
        return 1
    finally:
        db.close()


def _clean_property(db, property_uuid: str) -> dict[str, int]:
    """Delete the weespas rows keyed to one seeded property_uuid, FK-safe (leaves → Property).
    Structural flags aren't FK'd to the Property, so they're keyed by the same (aoi, building) the
    link carries — scoped to buildings THIS property_uuid links, so a shared footprint's genuine
    flag is never touched. Caller owns the commit."""
    counts: dict[str, int] = {}
    # StructuralFlags on buildings linked by exactly this seeded property (never a broader match).
    counts["structural_flag"] = db.execute(text(
        "delete from structural_flag where (aoi_code, insar_building_id) in "
        "(select aoi_code, insar_building_id from building_link where listing_id = :pid) "
        "and source = 'e2e'"
    ), {"pid": property_uuid}).rowcount
    counts["building_link"] = db.execute(text(
        "delete from building_link where listing_id = :pid"
    ), {"pid": property_uuid}).rowcount
    counts["properties"] = db.execute(text(
        "delete from properties where id = :pid"
    ), {"pid": property_uuid}).rowcount
    return counts


def _clean(run: str) -> int:
    _reject_unsafe_run(run)
    db = SessionLocal()
    try:
        # Every Property this run seeded carries the run id in its title; each such id is also its
        # property_uuid (the link/flag key). Resolve them, then delete FK-safe per property.
        ids = [r[0] for r in db.execute(text(
            "select id from properties where title like :mid"
        ), {"mid": f"%{run}%"}).fetchall()]
        total = 0
        for pid in ids:
            for _, n in _clean_property(db, pid).items():
                total += n
        db.commit()
        print(f"clean {run}: removed {total} weespas rows across {len(ids)} seeded properties")
        return 0
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        print(f"clean {run} FAILED, rolled back: {exc}", file=sys.stderr)
        return 1
    finally:
        db.close()


def main() -> int:
    argv = sys.argv[1:]
    if not argv:
        print(__doc__, file=sys.stderr)
        return 2
    cmd = argv[0]
    if cmd == "seed":
        if len(argv) < 5:
            print("usage: seed_shop_on_map.py seed <run> <aoi> <building_id> <property_uuid> [--confirmed]",
                  file=sys.stderr)
            return 2
        run, aoi, building_raw, property_uuid = argv[1], argv[2], argv[3], argv[4]
        confirmed = "--confirmed" in argv[5:]
        try:
            building_id = int(building_raw)
        except ValueError:
            print(f"building_id must be an integer, got {building_raw!r}", file=sys.stderr)
            return 2
        return _seed(run, aoi, building_id, property_uuid, confirmed)
    if cmd == "clean":
        if len(argv) != 2:
            print("usage: seed_shop_on_map.py clean <run>", file=sys.stderr)
            return 2
        return _clean(argv[1])
    print(f"unknown command {cmd!r} (want 'seed' or 'clean')", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
