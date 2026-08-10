"""Opt-in PostGIS fidelity test.

Skipped unless COMMERCE_TEST_PG_URL points at a real Postgres+PostGIS DB. Runs the SAME
radius assertions against ST_DWithin to prove the prod path agrees with the SQLite
Haversine path within the sphere/spheroid tolerance.

Run: COMMERCE_TEST_PG_URL=postgresql+psycopg2://commerce:commerce@localhost:5432/commerce_test \\
       .venv/bin/pytest PE/commerce/tests/test_proximity_postgis.py -m postgis
"""
import os
from datetime import datetime, timezone

import pytest

_PG_URL = os.getenv("COMMERCE_TEST_PG_URL")

pytestmark = [
    pytest.mark.postgis,
    pytest.mark.skipif(not _PG_URL, reason="COMMERCE_TEST_PG_URL not set"),
]

_LAT, _LNG = -1.2921, 36.8219


@pytest.fixture
def pg_session():
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker

    from PE.commerce.core.database import Base
    import PE.commerce.models  # noqa: F401

    engine = create_engine(_PG_URL)
    with engine.begin() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS postgis"))
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    try:
        yield db
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_st_dwithin_matches_haversine(pg_session):
    from PE.commerce.models.listing import Listing
    from PE.commerce.models.seller import Seller, Shop
    from PE.commerce.services import proximity

    seller = Seller(user_uuid="u", display_name="S")
    pg_session.add(seller)
    pg_session.flush()
    shop = Shop(seller_id=seller.id, name="S")
    proximity.set_location(shop, _LAT, _LNG)
    pg_session.add(shop)
    pg_session.flush()

    near = Listing(shop_id=shop.id, seller_id=seller.id, title="near",
                   price_cents=100, currency="KES", stock_qty=5,
                   created_at=datetime.now(timezone.utc))
    proximity.set_location(near, _LAT + 1.0 / 111.32, _LNG)  # ~1 km
    far = Listing(shop_id=shop.id, seller_id=seller.id, title="far",
                  price_cents=100, currency="KES", stock_qty=5,
                  created_at=datetime.now(timezone.utc))
    proximity.set_location(far, _LAT + 50.0 / 111.32, _LNG)  # ~50 km
    pg_session.add_all([near, far])
    pg_session.commit()

    found = proximity.search_listings(pg_session, _LAT, _LNG, 2000.0, limit=100)
    assert {li.title for li, _ in found} == {"near"}
    _, dist = found[0]
    assert 900 < dist < 1100  # ST_Distance metres agree with Haversine within tolerance
