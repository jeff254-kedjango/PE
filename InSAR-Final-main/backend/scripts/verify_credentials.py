"""
Pre-flight check before we commit hours of HyP3 compute.

Verifies, in order:
  1. backend/.env loads and EARTHDATA_USER / EARTHDATA_PASS are present
  2. asf_search.geo_search returns >0 SLC scenes over Huruma in a 60-day window
  3. HyP3() authentication succeeds and the account has remaining job quota

Prints scene counts and quota. Never prints the password. Never writes to disk.

Run from backend/:
    python -m scripts.verify_credentials
"""

from __future__ import annotations

import os
import sys
from datetime import date, timedelta
from pathlib import Path

from dotenv import load_dotenv


BACKEND_DIR = Path(__file__).resolve().parents[1]


def _step(n: int, msg: str) -> None:
    print(f"[{n}/3] {msg}")


def _fail(msg: str) -> None:
    print(f"  ✗ {msg}", file=sys.stderr)
    sys.exit(1)


def _ok(msg: str) -> None:
    print(f"  ✓ {msg}")


def check_env() -> tuple[str, str]:
    _step(1, "Loading credentials from backend/.env")
    env_path = BACKEND_DIR / ".env"
    if not env_path.exists():
        _fail(f"{env_path} not found")
    load_dotenv(env_path)
    user = os.environ.get("EARTHDATA_USER", "").strip()
    pw = os.environ.get("EARTHDATA_PASS", "").strip()
    if not user:
        _fail("EARTHDATA_USER missing from .env")
    if not pw:
        _fail("EARTHDATA_PASS missing from .env")
    _ok(f"user={user[:3]}*** (password length={len(pw)})")
    return user, pw


def check_asf_search() -> None:
    _step(2, "Querying ASF for Sentinel-1 SLC scenes over Huruma (last 60 days)")
    import asf_search as asf
    from scripts.aois import HURUMA, bbox

    minlon, minlat, maxlon, maxlat = bbox(HURUMA)
    wkt = (
        f"POLYGON(("
        f"{minlon} {minlat}, {maxlon} {minlat}, {maxlon} {maxlat}, "
        f"{minlon} {maxlat}, {minlon} {minlat}))"
    )
    end = date.today()
    start = end - timedelta(days=60)
    try:
        results = asf.geo_search(
            intersectsWith=wkt,
            platform=asf.PLATFORM.SENTINEL1,
            processingLevel=asf.PRODUCT_TYPE.SLC,
            beamMode=asf.BEAMMODE.IW,
            start=start.isoformat(),
            end=end.isoformat(),
        )
    except Exception as e:  # noqa: BLE001
        _fail(f"asf_search.geo_search failed: {e}")
        return
    n = len(results)
    if n == 0:
        _fail("0 scenes returned — bbox or date window may be wrong")
    paths = sorted({int(r.properties.get("pathNumber", -1)) for r in results})
    flights = sorted({r.properties.get("flightDirection", "?") for r in results})
    _ok(f"{n} scenes; paths={paths}; flight_directions={flights}")


def check_hyp3_auth(user: str, pw: str) -> None:
    _step(3, "Authenticating with HyP3")
    try:
        from hyp3_sdk import HyP3
        hyp3 = HyP3(username=user, password=pw)
        info = hyp3.my_info()
    except Exception as e:  # noqa: BLE001
        _fail(f"HyP3 auth failed: {e}")
        return
    remaining = info.get("remaining_credits")
    quota = info.get("quota", {})
    _ok(f"authenticated; remaining_credits={remaining}; quota={quota}")


def main() -> None:
    user, pw = check_env()
    check_asf_search()
    check_hyp3_auth(user, pw)
    print("\nAll checks passed. Ready to run the pipeline.")


if __name__ == "__main__":
    main()
