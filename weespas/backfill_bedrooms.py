#!/usr/bin/env python3
"""
Backfill bedrooms/bathrooms for properties that have NULL values.

Parses the title to extract bedroom count (e.g., "2-Bed", "3-Bedroom").
Infers bathrooms based on bedrooms count:
  - 1 bed  -> 1 bath
  - 2 beds -> 1 bath
  - 3 beds -> 1 bath
  - 4 beds -> 2 baths (larger homes)
  - 5 beds -> 3 baths
  - 6+ beds -> 4 baths
  - If price is very high relative to category, add an extra bathroom

Properties like land, kiosk, warehouse, shop, office get 0 bedrooms / 0 bathrooms
unless they already have values.
"""

import sys
import os
import re

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from sqlalchemy.orm import Session
from PE.weespas.core.database import SessionLocal
from PE.weespas.models.property import Property, PropertyCategory


# Categories that don't have bedrooms
NON_RESIDENTIAL = {"land", "kiosk", "warehouse", "shop", "office", "commercial_space", "container", "stall"}

# Price thresholds (KES) that suggest extra bathrooms for the bedroom count
HIGH_PRICE_SALE = {
    1: 8_000_000,
    2: 15_000_000,
    3: 25_000_000,
    4: 40_000_000,
    5: 60_000_000,
}

HIGH_PRICE_RENT = {
    1: 80_000,
    2: 120_000,
    3: 150_000,
    4: 200_000,
    5: 300_000,
}


def extract_bedrooms_from_title(title: str) -> int | None:
    """Parse title for bedroom count. Returns None if not found."""
    # Match patterns like "2-Bed", "3-Bedroom", "4 Bed", "2 bed"
    match = re.search(r'(\d+)\s*-?\s*bed(?:room)?', title, re.IGNORECASE)
    if match:
        return int(match.group(1))
    return None


def infer_bathrooms(bedrooms: int, price: float, is_rent: bool) -> int:
    """Infer bathroom count from bedrooms, price, and listing type."""
    if bedrooms == 0:
        return 1  # Studios/bedsitters still have a bathroom

    # Base bathroom count
    if bedrooms <= 3:
        baths = 1
    elif bedrooms == 4:
        baths = 2
    elif bedrooms == 5:
        baths = 3
    else:
        baths = 4

    # If price is high for the bedroom count, add an extra bathroom
    thresholds = HIGH_PRICE_RENT if is_rent else HIGH_PRICE_SALE
    threshold = thresholds.get(bedrooms)
    if threshold and price > threshold:
        baths += 1

    return baths


def backfill():
    db = SessionLocal()
    try:
        # Load category slugs
        cat_map = {}
        for cat in db.query(PropertyCategory).all():
            cat_map[cat.id] = cat.slug

        properties = db.query(Property).filter(Property.is_active == True).all()
        updated = 0

        for prop in properties:
            cat_slug = cat_map.get(prop.category_id, "")
            changed = False

            # Skip non-residential categories — set to 0 if NULL
            if cat_slug in NON_RESIDENTIAL:
                if prop.bedrooms is None:
                    prop.bedrooms = 0
                    changed = True
                if prop.bathrooms is None:
                    prop.bathrooms = 0
                    changed = True
                if changed:
                    updated += 1
                    print(f"  [non-res] {prop.title}: beds={prop.bedrooms}, baths={prop.bathrooms}")
                continue

            # For residential: try to extract bedrooms from title if NULL
            if prop.bedrooms is None:
                beds = extract_bedrooms_from_title(prop.title)
                if beds is not None:
                    prop.bedrooms = beds
                    changed = True
                else:
                    # Default: studio-like = 0 for "studio" category, else 1
                    prop.bedrooms = 0 if cat_slug == "studio" else 1
                    changed = True

            # Infer bathrooms if NULL
            if prop.bathrooms is None:
                is_rent = prop.listing_type and prop.listing_type.value == "rent"
                price = float(prop.price) if prop.price else 0
                prop.bathrooms = infer_bathrooms(prop.bedrooms, price, is_rent)
                changed = True

            if changed:
                updated += 1
                print(f"  {prop.title}: beds={prop.bedrooms}, baths={prop.bathrooms}")

        db.commit()
        print(f"\nDone! Updated {updated} properties.")

    finally:
        db.close()


if __name__ == "__main__":
    backfill()
