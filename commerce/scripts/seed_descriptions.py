"""Seed multi-paragraph descriptions onto existing PRODUCT listings — a test-data utility so the
§8 "read more" expander has something long enough to truncate.

Idempotent + non-destructive by design:
  * touches only PRODUCT listings (never posts — a post's body IS its description, sacred);
  * touches only rows whose description is NULL or shorter than ``_MIN_REAL_LEN`` — so a seller's
    real description (any reasonable length) is NEVER overwritten, and a re-run is a clean no-op;
  * writes a long (>150 char) two-paragraph blurb derived from the listing's own title, so the
    feed visibly demonstrates the preview + "read more" without inventing fake products.

Run (live PG):  PYTHONPATH=/home/jeff /home/jeff/PE/commerce/.venv/bin/python -m PE.commerce.scripts.seed_descriptions
"""
from __future__ import annotations

import logging

from PE.commerce.core.database import SessionLocal
from PE.commerce.models.listing import POST_KIND_PRODUCT, Listing

logger = logging.getLogger("seed_descriptions")

# A real seller description of at least this many chars is left untouched (don't clobber genuine
# text); anything shorter/absent is a candidate for the demo blurb. Comfortably above the noise of
# a one-word stub, comfortably below a real paragraph.
_MIN_REAL_LEN = 40


def _blurb(title: str) -> str:
    """A >150-char, two-paragraph description derived from the listing title. The blank line makes
    the frontend render two <p> blocks; the length guarantees the 150-char preview truncates."""
    name = (title or "this item").strip() or "this item"
    return (
        f"{name} — fresh stock, ready today. Sourced locally and checked for quality before it "
        f"reaches you, so what you see on the feed is exactly what you collect. Quantities are "
        f"limited and move fast at this price.\n\n"
        f"Message the seller to confirm availability, agree a pickup time, or ask about bulk "
        f"orders. Delivery within the neighbourhood can be arranged — just ask when you reach out."
    )


def seed(db) -> int:
    """Set the demo blurb on every product listing lacking a real description. Returns the count
    updated. Commits once. Caller owns the session lifecycle."""
    candidates = (
        db.query(Listing)
        .filter(Listing.post_kind == POST_KIND_PRODUCT)
        .all()
    )
    updated = 0
    for li in candidates:
        current = (li.description or "").strip()
        if len(current) >= _MIN_REAL_LEN:
            continue  # genuine description — never clobber
        li.description = _blurb(li.title)
        updated += 1
    if updated:
        db.commit()
    return updated


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    db = SessionLocal()
    try:
        n = seed(db)
        logger.info("seeded descriptions on %d product listing(s)", n)
    finally:
        db.close()


if __name__ == "__main__":
    main()
