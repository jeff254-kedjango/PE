"""External structural-flag loader — the seam where Weespas's engineer/authority
judgements enter the InSAR build.

InSAR sees ground/surface MOTION; it is physically blind to construction quality
(bad concrete, missing rebar, illegal floors) — the dominant Nairobi collapse
driver. The `structural_flag` table in Weespas is that orthogonal "second sensor":
an engineer or authority marks a *specific* building UNSAFE / AUTH_UNSAFE / CLEARED.
This module loads those judgements at build time and aligns them to the InSAR
building ids, so `postprocess.composite_risk` / `danger_level` can fuse them.

CONTRACT (deliberately decoupled — InSAR never imports the Weespas app):
  - The flags arrive as a small JSON export at `data/structural_flags/<aoi>.json`,
    which a Weespas→InSAR sync job writes (and which an automatic NCA/enforcement
    feed can write to the same path later — no code change here). Shape:
        {
          "as_of": "2026-06-22",                # date the export was taken
          "flags": {
            "<building_id>": {"state": 2, "observed_at": "2025-01-10", "source": "engineer"},
            ...
          }
        }
  - If the export is ABSENT (synthetic-only checkout, no sync yet), every building
    resolves to STRUCT_NONE with NaN age ⇒ scoring is byte-identical to the
    motion-only path. Absence of a flag is "uninspected", never "cleared".
  - Unknown / malformed entries fail SAFE to STRUCT_NONE (never to a CLEARED that
    would silence a building). A bad export can only ever fail to *raise* risk; it
    can never spuriously lower it.

O(1) per building: one dict lookup per id, no per-row Python beyond the alignment
loop the caller already runs.
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import numpy as np

from scripts.postprocess import (
    STRUCT_NONE,
    STRUCT_CLEARED,
    STRUCT_UNSAFE,
    STRUCT_AUTH_UNSAFE,
)

BACKEND_DIR = Path(__file__).resolve().parents[1]
FLAGS_DIR = BACKEND_DIR / "data" / "structural_flags"

_VALID_STATES = {STRUCT_NONE, STRUCT_CLEARED, STRUCT_UNSAFE, STRUCT_AUTH_UNSAFE}


def _flags_path(aoi_code: str) -> Path:
    return FLAGS_DIR / f"{aoi_code}.json"


def fetch_structural_flags(
    aoi_code: str,
    building_ids: np.ndarray,
    *,
    as_of: date | None = None,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Resolve external structural flags for `building_ids` (in order).

    Returns four arrays aligned to `building_ids`:
      - state         (uint8)   : STRUCT_NONE/CLEARED/UNSAFE/AUTH_UNSAFE
      - age_days      (float64) : days from observed_at to `as_of`; NaN if no flag
                                  (NaN ⇒ composite_risk treats a clearance as fresh,
                                  but state==NONE makes age moot for unflagged rows)
      - observed_at   (object)  : datetime.date or None per building (for the parquet
                                  `structural_flag_observed_at` column)
      - source        (object)  : 'engineer' | 'authority' | None (for the parquet
                                  `structural_flag_source` column)

    No file ⇒ all-NONE arrays (regression-safe). `as_of` defaults to the export's
    own `as_of` field, else today; it only affects age (and only for CLEARED rows).
    """
    n = len(building_ids)
    state = np.full(n, STRUCT_NONE, dtype=np.uint8)
    age_days = np.full(n, np.nan, dtype=np.float64)
    observed_at: list[date | None] = [None] * n
    source: list[str | None] = [None] * n

    path = _flags_path(aoi_code)
    if not path.exists():
        return state, age_days, observed_at, source

    try:
        doc = json.loads(path.read_text())
        flags = doc.get("flags", {}) or {}
        export_as_of = doc.get("as_of")
    except (json.JSONDecodeError, OSError, AttributeError):
        # Malformed export: fail SAFE — behave as if there were no flags at all.
        return state, age_days, observed_at, source

    ref = as_of or (date.fromisoformat(export_as_of) if export_as_of else None)
    if ref is None:
        ref = date.today()

    for i in range(n):
        rec = flags.get(str(int(building_ids[i])))
        if not isinstance(rec, dict):
            continue
        st = rec.get("state", STRUCT_NONE)
        if st not in _VALID_STATES or st == STRUCT_NONE:
            continue  # unknown/none → leave as NONE (fail-safe; never auto-clear)
        state[i] = st
        source[i] = rec.get("source")
        obs_raw = rec.get("observed_at")
        if obs_raw:
            try:
                obs = date.fromisoformat(obs_raw)
                observed_at[i] = obs
                age_days[i] = float((ref - obs).days)
            except ValueError:
                pass  # bad date → keep state, leave age NaN (treated as fresh)

    return state, age_days, observed_at, source
