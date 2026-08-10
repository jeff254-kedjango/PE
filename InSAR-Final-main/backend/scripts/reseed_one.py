"""Re-seed a SINGLE synthetic/partial AOI and rebuild the DB views.

Thin CLI around `seed_synthetic.reseed_aoi`, so the Celery `rebuild_aoi` task can
shell out to it the same way every other CPU stage shells out to a `scripts.<x>`
module (keeps the worker process free of heavy seeding imports, and keeps the
seeder the single source of truth for synthetic data + the provenance guard).

    python -m scripts.reseed_one --aoi huruma

The provenance safety gate lives in reseed_aoi: an 'insar' AOI is SKIPPED (real
data is never overwritten by synthetic) unless --force is given.
"""
from __future__ import annotations

import argparse
import sys

from scripts.seed_synthetic import reseed_aoi


def main() -> int:
    p = argparse.ArgumentParser(description="Re-seed one synthetic AOI + rebuild DB")
    p.add_argument("--aoi", required=True, help="AOI code (e.g. huruma)")
    p.add_argument("--force", action="store_true",
                   help="overwrite even an 'insar' (real-data) AOI — dangerous")
    args = p.parse_args()
    seeded = reseed_aoi(args.aoi, force=args.force, rebuild_db=True)
    if not seeded:
        print(f"{args.aoi}: skipped (provenance 'insar'; real data not overwritten)",
              file=sys.stderr)
        # Not an error — the guard did its job. Exit 0 so the task succeeds.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
