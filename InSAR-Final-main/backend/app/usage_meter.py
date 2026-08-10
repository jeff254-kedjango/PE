"""Server-side access metering: make direct data-API pulls visible to Weespas's §8.

The frontend already meters *building clicks* (insar_building_view) and *exports*. But a
script that pulls /aoi/{code}/bundle directly with a valid token gets the entire AOI dataset
and emits ZERO frontend telemetry — so bulk exfiltration by an authenticated account is
invisible to company-detection. This closes that gap: the bundle endpoint itself reports a
lightweight `insar_bundle_fetch` to Weespas, attributed to the token's user.

Posture:
  * **Inert without WEESPAS_TELEMETRY_URL** — the read app keeps its "no live network"
    default; metering only happens when a sink is wired (prod / integrated dev).
  * **Best-effort, non-blocking, never fails the request** — it runs in a background task
    AFTER the bundle response is sent, with a short timeout, swallowing all errors. A
    metering hiccup must never slow or break a map load.
  * **Replays the caller's own bearer** — same trust path as the frontend telemetry: the
    token proves "user X", Weespas's sink records it under that user. We never mint anything.
  * **Bundle endpoint only** — NOT /buildings/at-date (the time-slider animation hammers it;
    metering that would fabricate volume for ordinary users). The bundle is fetched once per
    AOI-open (browser-cached after), so it is naturally low-rate for a human and high only
    for an AOI-sweeping scraper.
"""
from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

# Full URL of Weespas's InSAR telemetry sink, e.g.
# http://localhost:8000/api/v1/insar-telemetry/event. Empty ⇒ metering disabled.
WEESPAS_TELEMETRY_URL = os.getenv("WEESPAS_TELEMETRY_URL", "").strip()
# Keep the outbound call short — this is fire-and-forget, never worth waiting on.
_TIMEOUT_S = float(os.getenv("WEESPAS_TELEMETRY_TIMEOUT_S", "2.0"))


def usage_metering_enabled() -> bool:
    return bool(WEESPAS_TELEMETRY_URL)


def meter_bundle_fetch(authorization: str | None, aoi_code: str) -> None:
    """Report one bundle pull to Weespas. Inert without a sink URL or a bearer token;
    swallows every error. Intended to run in a FastAPI/Starlette background task."""
    if not WEESPAS_TELEMETRY_URL or not authorization:
        return
    try:
        import httpx  # local import: only loaded when metering is actually wired

        httpx.post(
            WEESPAS_TELEMETRY_URL,
            json={"action": "insar_bundle_fetch", "aoi_code": aoi_code},
            headers={"Authorization": authorization},
            timeout=_TIMEOUT_S,
        )
    except Exception as exc:  # best-effort — a failed beat must never disturb the map
        logger.warning("usage metering POST failed (ignored): %s", exc)
