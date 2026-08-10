"""Build-time pipeline orchestration (Celery + Redis).

This package orchestrates the EXISTING, idempotent pipeline scripts
(`scripts.clip_to_common_grid`, `reproject_hyp3`, `mintpy_run`, `fetch_gacos`,
`hyp3_pipeline`, `join_insar`) as a managed task graph. It does NOT reimplement
any pipeline logic — each task shells out to the same `python -m scripts.<x>`
entry point an operator would run by hand, so the scripts stay the single source
of truth and keep their logging, retry, and self-healing behaviour.

Design constraints (see ../../analysis_two.md, Phase 2):
  * The READ-PATH SERVING APP (`app/main.py`) is never imported or modified here.
    Celery touches only build-time work. The running FastAPI keeps serving its
    in-RAM bundles untouched; a rebuild ends with an atomic `demo.duckdb` swap
    that the live reader picks up on its next connection.
  * Orchestration is fixed-step per AOI (O(1) in tasks per stage) — no per-building
    or per-pixel work is added at this layer.
  * MintPy/ISCE cannot run on the laptop, so the SBAS step runs on ASF
    OpenSARLab. The `mintpy` task is therefore a *gate*: it verifies the expected
    HDF5 outputs exist and otherwise raises a clear "awaiting OpenSARLab" error,
    pausing the chain deterministically rather than fabricating data.
"""
