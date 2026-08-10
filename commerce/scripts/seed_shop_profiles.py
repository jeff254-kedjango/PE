"""Seed seller-published business cards (description + contact + category) onto existing SHOPS — a
test-data utility so the §8 shop-profile hovercard and the trending rail have something to show.

Idempotent + non-destructive, same discipline as seed_descriptions:
  * skips the auto-provisioned personal-timeline shop (name == "My timeline") — a post's home is
    not a curated storefront and should stay blank;
  * sets ``description`` only when it is NULL/shorter than ``_MIN_REAL_LEN`` (never clobbers a real
    seller blurb), ``contact`` only when it is NULL (never overwrites a published contact), and
    ``category`` only when it is NULL (never overwrites a seller's real choice);
  * the demo blurb is derived from the shop's own name and kept WELL under the 200-word cap;
  * the category is a stable sha1(shop_id)-derived pick (no RNG) so re-runs are reproducible.

Run (live PG):  PYTHONPATH=/home/jeff /home/jeff/PE/commerce/.venv/bin/python -m PE.commerce.scripts.seed_shop_profiles
"""
from __future__ import annotations

import hashlib
import logging

from PE.commerce.core.categories import SHOP_CATEGORIES
from PE.commerce.core.database import SessionLocal
from PE.commerce.models.seller import Shop
from PE.commerce.services.catalog import _PERSONAL_SHOP_NAME

logger = logging.getLogger("seed_shop_profiles")

# A real description of at least this many chars is left untouched.
_MIN_REAL_LEN = 40
# A neutral demo contact line (clearly a placeholder, not a real number).
_DEMO_CONTACT = "WhatsApp 0700 000 000"


def _category_for(shop_id: str) -> str:
    """A stable, evenly-spread category for a shop, derived from its id so the same shop always
    gets the same category across re-runs (idempotent). Deterministic — no RNG (which the project
    bans for reproducibility) — via a sha1 of the id modulo the taxonomy size."""
    digest = hashlib.sha1(str(shop_id).encode("utf-8")).hexdigest()
    return SHOP_CATEGORIES[int(digest, 16) % len(SHOP_CATEGORIES)]


def _blurb(name: str) -> str:
    """A short (well under 200 words) "about this shop" blurb derived from the shop name."""
    shop = (name or "This shop").strip() or "This shop"
    return (
        f"{shop} sells fresh, locally-sourced stock to the neighbourhood. We restock through the "
        f"week and price fairly — message us to confirm what's available, agree a pickup, or ask "
        f"about a bulk order. Delivery nearby can be arranged."
    )


def seed(db) -> int:
    """Publish the demo business card on every non-personal shop lacking one. Returns the count
    updated. Commits once."""
    shops = db.query(Shop).filter(Shop.name != _PERSONAL_SHOP_NAME).all()
    updated = 0
    for shop in shops:
        changed = False
        if len((shop.description or "").strip()) < _MIN_REAL_LEN:
            shop.description = _blurb(shop.name)
            changed = True
        if not (shop.contact or "").strip():
            shop.contact = _DEMO_CONTACT
            changed = True
        # Assign a category only when unset (never clobber a seller's real choice) so the trending
        # rail + feed cards have category colors to render.
        if not (shop.category or "").strip():
            shop.category = _category_for(shop.id)
            changed = True
        if changed:
            updated += 1
    if updated:
        db.commit()
    return updated


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    db = SessionLocal()
    try:
        n = seed(db)
        logger.info("seeded business cards on %d shop(s)", n)
    finally:
        db.close()


if __name__ == "__main__":
    main()
