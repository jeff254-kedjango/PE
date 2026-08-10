"""One-time backfill of InSAR footprint-verification for existing listings.

Resolves every active listing against the InSAR footprints and stamps its
`verification_status` — silently (no inbox notifications). Idempotent / re-runnable.

Run once after applying the verification_status migration, with the InSAR DuckDB
configured (INSAR_DUCKDB_PATH set) so listings can actually resolve:

    PYTHONPATH=/home/jeff .venv/bin/python -m PE.weespas.scripts.backfill_insar_verification

Prints a tally of the outcome. If INSAR_DUCKDB_PATH is unset every row comes back
'unavailable' (honest) — set it and re-run to light up the catalog.
"""
from __future__ import annotations

import sys

from PE.weespas.services.insar_backfill import backfill_verification


def main() -> int:
    tally = backfill_verification()
    if not tally:
        print("No active listings to verify.")
        return 0
    print("InSAR verification backfill complete:")
    for coverage in sorted(tally):
        print(f"  {coverage:>14} : {tally[coverage]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
