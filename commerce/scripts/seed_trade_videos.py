"""Seed REAL, PLAYABLE short-video clips onto existing short-video listings — so the §8 Trade
"Videos" lane (and the vertical short-video player it opens) has actual footage during dev/demo.

WHY this exists: the trade feed already had ~25 listings flagged ``is_short_video`` near the demo
centre, but they carried only IMAGE media (the Videos lane filters to video URLs and so showed
almost nothing), and the handful of ``/uploads/trade/videos/*.mp4`` files on disk were 24-byte
stubs that no browser can decode. This script fills both gaps with synthesised-but-real clips.

WHAT it does (idempotent + non-destructive):
  * renders a small set of short MP4 clips ONCE into the WEESPAS uploads dir
    (``uploads/trade/videos/``) with ffmpeg — the same place the real upload pipeline writes, so
    the frontend resolves them via resolveMediaUrl exactly like real uploads. Nothing binary is
    committed to git; a clip already on disk is reused (no re-render);
  * attaches a clip URL to each ``is_short_video`` PRODUCT listing near the demo centre that does
    NOT already have a playable video in its media_urls — PREPENDING the video so the carousel /
    video lane shows it first, and PRESERVING any existing image media (never clobbers real media);
  * leaves a listing that already has a real video URL untouched (clean re-run no-op).

Run (live PG):
  PYTHONPATH=/home/jeff /home/jeff/PE/commerce/.venv/bin/python -m PE.commerce.scripts.seed_trade_videos

Env knobs:
  WEESPAS_UPLOADS_DIR  override the uploads root (default: /home/jeff/PE/weespas/uploads)
  SEED_VIDEOS_LAT/LNG  demo centre (default Nairobi CBD -1.2921, 36.8219)
  SEED_VIDEOS_RADIUS_M radius around the centre to seed (default 60000 m)
  SEED_VIDEOS_LIMIT    cap on listings to touch (default 40)
"""
from __future__ import annotations

import json
import logging
import math
import os
import shutil
import subprocess
from pathlib import Path

from PE.commerce.core.database import SessionLocal
from PE.commerce.models.listing import POST_KIND_PRODUCT, Listing
from PE.commerce.services.proximity import _M_PER_DEG

logger = logging.getLogger("seed_trade_videos")

# Where the weespas media pipeline serves /uploads from (StaticFiles mount in weespas/main.py).
_UPLOADS_DIR = Path(os.environ.get("WEESPAS_UPLOADS_DIR", "/home/jeff/PE/weespas/uploads"))
_TRADE_VIDEOS_SUBDIR = Path("trade") / "videos"  # mirrors the real upload pipeline's layout

_LAT = float(os.environ.get("SEED_VIDEOS_LAT", "-1.2921"))
_LNG = float(os.environ.get("SEED_VIDEOS_LNG", "36.8219"))
_RADIUS_M = float(os.environ.get("SEED_VIDEOS_RADIUS_M", "60000"))
_LIMIT = int(os.environ.get("SEED_VIDEOS_LIMIT", "40"))

# A small palette of distinct clips — each listing is assigned one deterministically by row id, so
# the lane shows variety and a re-run reuses the same on-disk file. Each entry: (filename, hue,
# label) → an ffmpeg-rendered 4 s 720x1280 (9:16) clip with a moving gradient + caption.
_CLIPS = [
    ("seed_trade_clip_01.mp4", "0x1f6f54", "Fresh stock"),
    ("seed_trade_clip_02.mp4", "0x8a4b2f", "Selling now"),
    ("seed_trade_clip_03.mp4", "0x355070", "New arrival"),
    ("seed_trade_clip_04.mp4", "0x6d2e6b", "In the shop"),
    ("seed_trade_clip_05.mp4", "0x2f6b3a", "Near you"),
]


def _is_video_url(url: str) -> bool:
    low = url.lower().split("?")[0].split("#")[0]
    if "/trade/videos/" in low:
        return True
    if "/trade/images/" in low:
        return False
    return low.endswith((".mp4", ".webm", ".mov", ".m4v", ".ogv"))


def _render_clip(dest: Path, hue: str, label: str) -> bool:
    """Render one real 9:16 MP4 with ffmpeg (a coloured background + drawn caption, 4 s). Returns
    True if a playable file exists at dest afterwards. A clip already on disk (>1 KB) is reused."""
    if dest.exists() and dest.stat().st_size > 1024:
        return True
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        logger.warning("ffmpeg not found — cannot render %s", dest.name)
        return False
    dest.parent.mkdir(parents=True, exist_ok=True)
    # A solid colour source with the label drawn centred. -y overwrites a partial/stub file.
    # H.264 + faststart so it streams + plays inline in every browser the feed targets.
    vf = (
        f"drawtext=text='{label}':fontcolor=white:fontsize=64:"
        f"x=(w-text_w)/2:y=(h-text_h)/2:box=1:boxcolor=black@0.35:boxborderw=20"
    )
    cmd = [
        ffmpeg, "-y", "-f", "lavfi", "-i", f"color=c={hue}:s=720x1280:d=4",
        "-vf", vf, "-r", "24", "-pix_fmt", "yuv420p",
        "-c:v", "libx264", "-movflags", "+faststart", str(dest),
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=120)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        logger.warning("ffmpeg failed for %s: %s", dest.name, exc)
        return False
    return dest.exists() and dest.stat().st_size > 1024


def _within(listing: Listing) -> bool:
    """Cheap bbox + Haversine gate so we only touch listings near the demo centre (the same metres
    semantics as the proximity feed)."""
    if listing.lat is None or listing.lng is None:
        return False
    deg = _RADIUS_M / _M_PER_DEG
    if abs(listing.lat - _LAT) > deg or abs(listing.lng - _LNG) > deg:
        return False
    rlat1, rlat2 = math.radians(listing.lat), math.radians(_LAT)
    dlat = math.radians(_LAT - listing.lat)
    dlng = math.radians(_LNG - listing.lng)
    a = math.sin(dlat / 2) ** 2 + math.cos(rlat1) * math.cos(rlat2) * math.sin(dlng / 2) ** 2
    return _M_PER_DEG * math.degrees(2 * math.asin(math.sqrt(a))) <= _RADIUS_M


_STUB_MAX_BYTES = 1024  # a video file at/below this is a dev stub (the old 24-byte placeholders)


def _heal_stub_videos(videos_dir: Path, ready: list[str]) -> int:
    """Earlier dev runs left 24-byte placeholder *.mp4 files on disk that no browser can decode, but
    listings already reference them by their hash name. Overwrite each such stub IN PLACE with a real
    rendered clip (copied from the palette) so the existing URLs play — no DB change, idempotent
    (a real file is left untouched). Returns the number healed."""
    if not videos_dir.is_dir() or not ready:
        return 0
    sources = [videos_dir / name for name in ready]
    healed = 0
    for mp4 in sorted(videos_dir.glob("*.mp4")):
        if mp4.name in ready:  # never clobber a palette clip with itself
            continue
        try:
            if mp4.stat().st_size > _STUB_MAX_BYTES:
                continue
        except OSError:
            continue
        # Deterministic source by filename so a re-run is stable; copy real bytes over the stub.
        shutil.copyfile(sources[hash(mp4.name) % len(sources)], mp4)
        healed += 1
    return healed


def seed(db) -> int:
    """Attach a playable clip URL to every nearby short-video product listing lacking one. Returns
    the count updated. Commits once. Caller owns the session lifecycle."""
    videos_dir = _UPLOADS_DIR / _TRADE_VIDEOS_SUBDIR
    # Render the clip palette up front; keep only the ones that actually produced a file.
    ready: list[str] = []
    for filename, hue, label in _CLIPS:
        if _render_clip(videos_dir / filename, hue, label):
            ready.append(filename)
    if not ready:
        logger.error("no clips available (ffmpeg missing/failed) — nothing to attach")
        return 0
    logger.info("clip palette ready: %d clip(s)", len(ready))

    # Heal pre-existing stub mp4s (e.g. the 24-byte placeholders) so URLs already in the DB play.
    healed = _heal_stub_videos(videos_dir, ready)
    if healed:
        logger.info("healed %d stub video file(s) in place", healed)

    candidates = (
        db.query(Listing)
        .filter(
            Listing.is_short_video.is_(True),
            Listing.post_kind == POST_KIND_PRODUCT,
            Listing.is_active.is_(True),
        )
        .order_by(Listing.created_at.desc())
        .all()
    )

    updated = 0
    for listing in candidates:
        if updated >= _LIMIT:
            break
        if not _within(listing):
            continue
        try:
            media = json.loads(listing.media_urls) if listing.media_urls else []
        except (ValueError, TypeError):
            media = []
        if not isinstance(media, list):
            media = []
        # Already has a playable video → leave it (clean re-run no-op).
        if any(isinstance(u, str) and _is_video_url(u) for u in media):
            continue
        # Deterministic clip choice by row id so each listing keeps the same clip across re-runs.
        clip = ready[hash(listing.id) % len(ready)]
        url = f"/uploads/trade/videos/{clip}"
        # PREPEND so the video lane / carousel leads with the clip; preserve existing images.
        listing.media_urls = json.dumps([url, *media])
        updated += 1

    if updated:
        db.commit()
    return updated


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    db = SessionLocal()
    try:
        n = seed(db)
        logger.info("attached playable clips to %d short-video listing(s)", n)
    finally:
        db.close()


if __name__ == "__main__":
    main()
