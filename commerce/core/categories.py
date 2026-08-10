"""Shop categories — the canonical taxonomy (§8 trending rail + feed card colors).

A shop's CATEGORY is what gives the trending rail its different-stimulus colors (a butchery reads
red, a bakery amber, …). The taxonomy lives here as the single backend source of truth: the API
validates a shop's declared category against this allow-list (an unknown value is a 422 at the
edge — never free-text into the rail, S-input-validation), and the feed/profile/trending DTOs
carry only the opaque SLUG.

The COLOR is deliberately NOT stored or returned by the backend — it is a pure presentation
concern derived client-side from the slug (frontend ``utils/categories.ts`` mirrors this list and
maps each slug to a CSS color token). Keeping the slug→color map out of the API means a palette
re-tune is a frontend-only change and the wire stays a stable, minimal contract.

A shop with NO category (None) is legal — an un-categorised / personal-timeline shop. The rail
falls back to a neutral treatment for those; only a declared, valid slug gets a category color.
"""
from __future__ import annotations

# The legal category slugs. Order is presentational-irrelevant (the frontend owns ordering of the
# picker), but kept stable so the parity test against the frontend list is deterministic.
SHOP_CATEGORIES: tuple[str, ...] = (
    "butchery",
    "bakery",
    "greengrocer",
    "restaurant",
    "boutique",
    "electronics",
    "shoes",
    "beauty",
    "hardware",
    "pharmacy",
    "general",
)

# A set for O(1) membership in the hot validation path.
_CATEGORY_SET = frozenset(SHOP_CATEGORIES)


def is_valid_category(value: str | None) -> bool:
    """True iff ``value`` is a known category slug. ``None`` (unset) is accepted by the schema
    separately — this guards only that a SUPPLIED category is one we recognise."""
    return value in _CATEGORY_SET
