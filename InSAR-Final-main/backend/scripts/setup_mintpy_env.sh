#!/usr/bin/env bash
# One-shot setup for the MintPy conda environment.
#
# Idempotent: re-running just verifies the env. Skip if `mintpy` env already
# exists. Assumes Miniforge or Miniconda is installed at $MINTPY_PREFIX
# (default: ~/miniforge3).
#
# After this finishes, `python -m scripts.mintpy_run --aoi huruma` will work
# from the backend/ venv — the wrapper invokes the conda env as a subprocess.

set -euo pipefail

MINTPY_PREFIX="${MINTPY_PREFIX:-$HOME/miniforge3}"
MINTPY_ENV="${MINTPY_ENV:-mintpy}"
CONDA="$MINTPY_PREFIX/bin/conda"

if [[ ! -x "$CONDA" ]]; then
    echo "error: conda not found at $CONDA" >&2
    echo "Install Miniforge first:" >&2
    echo "  curl -fsSL -o ~/Miniforge3.sh https://github.com/conda-forge/miniforge/releases/latest/download/Miniforge3-Linux-x86_64.sh" >&2
    echo "  bash ~/Miniforge3.sh -b -p $MINTPY_PREFIX" >&2
    exit 1
fi

if "$CONDA" env list | awk '{print $1}' | grep -qx "$MINTPY_ENV"; then
    echo "env '$MINTPY_ENV' already exists; verifying smallbaselineApp.py is on PATH…"
    "$CONDA" run -n "$MINTPY_ENV" --no-capture-output which smallbaselineApp.py
    exit 0
fi

echo "creating conda env '$MINTPY_ENV' with MintPy + deps (this takes a few minutes)…"
# Pin to conda-forge; install MintPy + ARIA-tools deps in one solve to avoid
# the cascading downgrade problem you get if you install them in stages.
"$CONDA" create -n "$MINTPY_ENV" -y -c conda-forge \
    python=3.11 \
    mintpy \
    isce2 \
    h5py \
    gdal \
    rasterio \
    geopandas \
    pyproj \
    shapely

echo
echo "verifying smallbaselineApp.py is callable…"
"$CONDA" run -n "$MINTPY_ENV" --no-capture-output smallbaselineApp.py --version

echo
echo "✓ env '$MINTPY_ENV' ready. Run:"
echo "    python -m scripts.mintpy_run --aoi huruma --track ASCENDING/57"
