"""One-time backfill: bound existing avatars to AVATAR_MAX_EDGE.

Avatars uploaded before the downscale landed (services/image_processing.py) were stored at
whatever the camera produced — measured on the dev DB: up to 4160x6240, 787 KB mean, 14.6 MB
across 19 users — and are served into circles no larger than 110px. New uploads are now bounded
by the transcode worker; this rewrites the ones already on disk.

Idempotent / re-runnable: an avatar already within bounds is skipped, and the `users.avatar` URL
is NEVER changed, so re-running is a no-op and no client cache is invalidated.

    PYTHONPATH=/home/jeff weespas/.venv/bin/python -m PE.weespas.scripts.backfill_avatar_downscale
    # add --dry-run to see what would change without writing

Two deliberate constraints:

  * **The URL stays the same.** Only the file's PIXELS change, in place. Rewriting the row to a
    new filename would 404 every already-cached client URL — the exact failure
    services/image_processing.py keeps the original source file to avoid.
  * **Only files under AVATAR_DIR are touched**, resolved and re-checked per path. `users.avatar`
    is DB-sourced text; treating it as a filesystem path without confinement would let a crafted
    value ('/uploads/avatars/../../etc/x') walk out of the media tree. It is written only by our
    own upload route today, so this is defence-in-depth rather than a live hole — but a backfill
    that writes to disk is exactly where that assumption should not be inherited on trust.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from PIL import Image

from PE.weespas.core.database import SessionLocal
from PE.weespas.models.user import User
from PE.weespas.routers.me import AVATAR_DIR
from PE.weespas.services.image_processing import AVATAR_MAX_EDGE

_PREFIX = "/uploads/avatars/"


def _safe_path(avatar_url: str) -> Path | None:
    """Map a stored avatar URL to a real file inside AVATAR_DIR, or None if it doesn't qualify.

    Rejects anything that isn't an avatar URL, and anything that escapes AVATAR_DIR after
    resolution (symlinks included).
    """
    if not avatar_url or not avatar_url.startswith(_PREFIX):
        return None
    candidate = AVATAR_DIR / Path(avatar_url[len(_PREFIX):]).name
    try:
        resolved = candidate.resolve()
        if not resolved.is_relative_to(AVATAR_DIR.resolve()):
            return None
    except OSError:
        return None
    return resolved if resolved.is_file() else None


def backfill(dry_run: bool = False) -> dict[str, int]:
    tally = {"resized": 0, "already_small": 0, "missing_file": 0, "unreadable": 0, "saved_kb": 0}

    db = SessionLocal()
    try:
        # Only the two columns needed — no reason to hydrate full User rows.
        rows = db.query(User.id, User.avatar).filter(User.avatar.isnot(None)).all()
    finally:
        db.close()

    for user_id, avatar_url in rows:
        path = _safe_path(avatar_url)
        if path is None:
            tally["missing_file"] += 1
            print(f"  MISSING  {user_id} -> {avatar_url}")
            continue

        try:
            before = path.stat().st_size
            with Image.open(path) as img:
                if max(img.size) <= AVATAR_MAX_EDGE:
                    tally["already_small"] += 1
                    continue
                old_size = img.size
                # Same format as the file on disk, so the extension in the (unchanged) URL keeps
                # matching the bytes served. Re-encoding a WebP at q=80 is fine; these are being
                # downscaled ~16x, so generational loss is irrelevant next to the resample.
                fmt = img.format
                out = img.convert("RGB")
                out.thumbnail((AVATAR_MAX_EDGE, AVATAR_MAX_EDGE), Image.LANCZOS)

            if dry_run:
                print(f"  WOULD    {user_id} {old_size} -> <={AVATAR_MAX_EDGE} ({before // 1024} KB)")
                tally["resized"] += 1
                continue

            # Write to a temp sibling then atomically replace, so an interrupted run can never
            # leave a truncated image being served.
            tmp = path.with_name(path.name + ".tmp")
            out.save(tmp, fmt, quality=80)
            tmp.replace(path)

            after = path.stat().st_size
            tally["resized"] += 1
            tally["saved_kb"] += max(0, (before - after)) // 1024
            print(f"  RESIZED  {user_id} {old_size} -> {out.size}  {before // 1024} -> {after // 1024} KB")
        except (OSError, ValueError) as exc:
            # Corrupt or unsupported file: report and move on. One bad avatar must not abort the
            # whole backfill.
            tally["unreadable"] += 1
            print(f"  FAILED   {user_id} {path.name}: {exc}")

    return tally


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true", help="report without writing")
    args = ap.parse_args()

    tally = backfill(dry_run=args.dry_run)
    print("\nAvatar downscale backfill" + (" (DRY RUN)" if args.dry_run else "") + ":")
    for k in ("resized", "already_small", "missing_file", "unreadable"):
        print(f"  {k:>14} : {tally[k]}")
    if not args.dry_run and tally["saved_kb"]:
        print(f"  {'saved':>14} : {tally['saved_kb'] / 1024:.1f} MB")
    return 1 if tally["unreadable"] else 0


if __name__ == "__main__":
    sys.exit(main())
