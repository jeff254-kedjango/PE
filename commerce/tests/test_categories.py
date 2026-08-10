"""Shop-category taxonomy: backend contract + cross-language parity with the frontend.

The category SLUG is the wire contract: the backend validates it (allow-list) and the frontend
maps it to a color. If the two category lists drift, a real backend slug renders on the frontend
as the neutral fallback with NO error — a silent bug. This test reads BOTH source-of-truth files
(this service's core/categories.py and the frontend utils/categories.ts) and asserts set-equality,
so any add/remove on either side fails until both are updated.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from PE.commerce.core.categories import SHOP_CATEGORIES, is_valid_category

# PE/commerce/tests/ → repo PE/ root, then the frontend sibling.
_FRONTEND_CATEGORIES = (
    Path(__file__).resolve().parents[2] / "weespas-frontend" / "src" / "utils" / "categories.ts"
)


def _frontend_slugs() -> list[str]:
    """Pull the slug keys out of the frontend CATEGORY_META object literal."""
    src = _FRONTEND_CATEGORIES.read_text(encoding="utf-8")
    block = re.search(r"CATEGORY_META[^=]*=\s*\{(.*?)\n\};", src, re.DOTALL)
    assert block, "could not locate CATEGORY_META in the frontend categories.ts"
    # Each entry is `slug: { label: ..., colorVar: ... }` — grab the leading bareword key.
    return re.findall(r"^\s*([a-z_]+):\s*\{", block.group(1), re.MULTILINE)


def test_backend_taxonomy_is_nonempty_and_unique():
    assert len(SHOP_CATEGORIES) > 0
    assert len(set(SHOP_CATEGORIES)) == len(SHOP_CATEGORIES), "duplicate category slug"


def test_is_valid_category():
    assert is_valid_category("butchery")
    assert not is_valid_category("not-a-real-slug")
    # None is NOT a valid supplied category (the schema accepts None separately as "unset").
    assert not is_valid_category(None)


@pytest.mark.skipif(not _FRONTEND_CATEGORIES.exists(), reason="frontend not checked out alongside")
def test_frontend_backend_parity():
    frontend = _frontend_slugs()
    assert frontend, "parsed no slugs from the frontend categories.ts"
    assert set(frontend) == set(SHOP_CATEGORIES), (
        "shop-category drift between backend SHOP_CATEGORIES and frontend CATEGORY_META: "
        f"backend-only={set(SHOP_CATEGORIES) - set(frontend)}, "
        f"frontend-only={set(frontend) - set(SHOP_CATEGORIES)}"
    )
