"""Per-AOI data provenance — synthetic seed vs. real InSAR join.

A single sidecar `data/provenance.json` records, per AOI code, the realness of
its currently-written partitions, on a three-state ladder:

  - `synthetic` — everything fabricated (legacy fully-synthetic seed).
  - `partial`   — real building footprints + real static terrain
                  (SoilGrids `soil_class`, OSM riparian/shoreline distance) but
                  velocity/coherence are still the synthetic stand-in, because
                  the real MintPy SBAS run (OpenSARLab) hasn't landed yet. This
                  is what `scripts/seed_synthetic.py` now writes.
  - `insar`     — real MintPy→footprint join; velocity is real too. Written by
                  `scripts/join_insar.py` once the SBAS products exist.

The bundle build (`app/main.py`) reads it into the header as `data_provenance`
so the frontend disclaimer states exactly what's real.

Why a sidecar and not an `aoi_registry` column: the registry is rewritten
wholesale by the seeder on every run, while the real join writes only the one
AOI it processed. A per-AOI JSON merged in place lets the two paths coexist —
running the real Huruma join flips *only* Huruma to `insar` and leaves a
synthetic Mombasa untouched. No schema migration, and it flips automatically
the moment the real join runs.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

Provenance = Literal["synthetic", "partial", "insar"]
_KNOWN: tuple[Provenance, ...] = ("synthetic", "partial", "insar")

_BACKEND_DIR = Path(__file__).resolve().parents[1]
PROVENANCE_PATH = _BACKEND_DIR / "data" / "provenance.json"


def set_provenance(aoi_code: str, value: Provenance) -> None:
    """Merge `{aoi_code: value}` into the sidecar, preserving other AOIs."""
    PROVENANCE_PATH.parent.mkdir(parents=True, exist_ok=True)
    current = load_all()
    current[aoi_code] = value
    PROVENANCE_PATH.write_text(json.dumps(current, indent=2, sort_keys=True) + "\n")


def load_all() -> dict[str, str]:
    """Whole sidecar as a dict; empty dict if it doesn't exist or is unreadable."""
    try:
        return json.loads(PROVENANCE_PATH.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def get_provenance(aoi_code: str) -> Provenance:
    """Provenance for one AOI; defaults to 'synthetic' when unrecorded.

    Defaulting to synthetic is the honest fallback: if we don't have positive
    evidence that real data was written, we must not over-claim. Any recorded
    value outside the known ladder also collapses to 'synthetic'.
    """
    val = load_all().get(aoi_code)
    return val if val in _KNOWN else "synthetic"
