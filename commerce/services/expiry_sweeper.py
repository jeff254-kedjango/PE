"""Expiry sweeper — periodically expire pending negotiations past their TTL (§7).

WHY A STANDALONE PROCESS (not Celery, not a FastAPI background task). Commerce keeps the lean
sync stack (architecture §4 reserves async/broker infra for the realtime mobility service), and
the trading layer already runs as separate processes per concern. A standalone loop:
  * isolates the sweep from request serving — a sweep that hangs or crashes can never take down
    checkout, and vice-versa;
  * is operated exactly like a Celery beat would be (you run one), but with zero broker;
  * reuses the existing sync ``SessionLocal`` + the concurrency-safe ``settlement.expire_stale``.

Run:
    python -m PE.commerce.services.expiry_sweeper          # loop forever (the dev/prod process)
    EXPIRY_SWEEP_INTERVAL_SECONDS=60 python -m ...         # override cadence

Concurrency. ``expire_stale`` is already safe against overlapping sweeps and live user
transitions (per-order commit + CAS — see its docstring). On Postgres we ALSO take a
``pg_try_advisory_lock`` so a second sweeper instance simply skips its tick rather than doing
redundant work; this is best-effort (a missing lock never blocks the sweep — correctness does
not depend on it, only efficiency). On SQLite (tests) the lock step is a no-op.
"""
from __future__ import annotations

import logging
import signal
import threading
import time

from sqlalchemy import text
from sqlalchemy.orm import Session

from PE.commerce.core.config import settings
from PE.commerce.core.database import SessionLocal
from PE.commerce.services import settlement

logger = logging.getLogger(__name__)

# A fixed 64-bit key for pg_try_advisory_lock — distinct per logical job. Any constant unique to
# "the commerce expiry sweep" works; this one is arbitrary but stable (a positive signed bigint,
# which is what pg_try_advisory_lock(bigint) expects).
_ADVISORY_LOCK_KEY = 0x0C0FFEE5E55E0001


def _try_advisory_lock(db: Session) -> bool:
    """On Postgres, try to grab the sweep's advisory lock without blocking. Returns True if we
    hold it (proceed) or if we're not on Postgres (no-op → always proceed). False means another
    sweeper holds it — skip this tick. Never raises into the caller (best-effort)."""
    if db.bind is None or db.bind.dialect.name != "postgresql":
        return True
    try:
        return bool(db.execute(
            text("SELECT pg_try_advisory_lock(:k)"), {"k": _ADVISORY_LOCK_KEY}
        ).scalar())
    except Exception:  # pragma: no cover - lock is an optimization, not a correctness gate
        logger.warning("advisory lock check failed; proceeding without it", exc_info=True)
        return True


def _advisory_unlock(db: Session) -> None:
    """Release the advisory lock on Postgres (no-op elsewhere). Swallows errors — the lock is
    session-scoped and would be released on disconnect anyway."""
    if db.bind is None or db.bind.dialect.name != "postgresql":
        return
    try:
        db.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": _ADVISORY_LOCK_KEY})
    except Exception:  # pragma: no cover
        logger.warning("advisory unlock failed", exc_info=True)


def run_once(db: Session) -> int:
    """One sweep pass: expire all pending negotiations past their TTL. Returns the count expired
    (0 if another sweeper holds the advisory lock, or nothing was stale). Caller owns the session
    lifecycle; this never closes ``db``."""
    if not _try_advisory_lock(db):
        logger.info("expiry sweep: another sweeper holds the lock; skipping this tick")
        return 0
    try:
        return settlement.expire_stale(db)
    finally:
        _advisory_unlock(db)


def _tick() -> int:
    """One self-contained tick with its OWN session, so a failure can't poison a long-lived
    session. Errors are logged and swallowed — one bad tick must not kill the loop."""
    db = SessionLocal()
    try:
        n = run_once(db)
        if n:
            logger.info("expiry sweep: expired %d pending negotiation(s)", n)
        return n
    except Exception:
        logger.exception("expiry sweep tick failed; will retry next interval")
        try:
            db.rollback()
        except Exception:  # pragma: no cover
            pass
        return 0
    finally:
        db.close()


def run_forever(interval_seconds: int | None = None) -> None:
    """Loop ``_tick`` every ``interval_seconds`` until SIGTERM/SIGINT. Uses an Event.wait so a
    shutdown signal interrupts the sleep immediately (no waiting out the full interval)."""
    interval = interval_seconds or settings.expiry_sweep_interval_seconds
    stop = threading.Event()

    def _handle(signum, _frame):
        logger.info("expiry sweeper: received signal %s, shutting down", signum)
        stop.set()

    signal.signal(signal.SIGTERM, _handle)
    signal.signal(signal.SIGINT, _handle)

    if not settings.expiry_sweep_enabled:
        logger.warning("expiry sweeper started but EXPIRY_SWEEP_ENABLED is false — idling as a no-op")
        # Still honour shutdown signals; just never sweep.
        stop.wait()
        return

    logger.info("expiry sweeper started (interval=%ds, ttl=%ds)",
                interval, settings.pending_ttl_seconds)
    while not stop.is_set():
        _tick()
        stop.wait(interval)
    logger.info("expiry sweeper stopped")


def main() -> None:  # pragma: no cover - process entrypoint
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    run_forever()


if __name__ == "__main__":  # pragma: no cover
    main()
