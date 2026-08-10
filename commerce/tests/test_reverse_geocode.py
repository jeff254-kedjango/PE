"""Reverse-geocode service tests (§8 Chunk C+).

Load-bearing properties:
  1. Seed is idempotent — calling ensure_seeded twice produces the same row set.
  2. A coord in a specific area returns the SPECIFIC area name, not the "Nairobi" catch-all.
  3. A coord in the Nairobi bbox but outside every specific rectangle returns "Nairobi".
  4. A coord outside every rectangle returns None.
  5. Bogus coords (NaN, out-of-range) return None cleanly (no exception).
  6. Priority tiebreak: if two overlapping specific rectangles both contain the point,
     the one with the LOWER priority number wins (more specific = higher priority = lower
     numeric).
"""
import math

import pytest

from PE.commerce.models.neighbourhood import Neighbourhood
from PE.commerce.services import reverse_geocode as rg


@pytest.fixture
def seeded(db_session):
    rg.ensure_seeded(db_session)
    return db_session


class TestEnsureSeeded:
    def test_seeds_rows(self, seeded):
        # ~32 rows in the seed (30 suburbs + catch-all + a couple more). Assert non-trivial size.
        assert seeded.query(Neighbourhood).count() >= 30

    def test_idempotent(self, db_session):
        rg.ensure_seeded(db_session)
        first_count = db_session.query(Neighbourhood).count()
        rg.ensure_seeded(db_session)
        second_count = db_session.query(Neighbourhood).count()
        assert first_count == second_count

    def test_catch_all_has_highest_priority_number(self, seeded):
        # The catch-all's priority (1000) is greater than every specific area's (10).
        catch_all = seeded.query(Neighbourhood).filter(Neighbourhood.slug == "nairobi").one()
        specific = seeded.query(Neighbourhood).filter(Neighbourhood.slug == "kilimani").one()
        assert catch_all.priority > specific.priority


class TestReverseGeocodeSpecificAreas:
    """A coord well inside a specific rectangle returns that area's display name."""

    def test_kilimani(self, seeded):
        # Roughly the middle of the Kilimani rectangle in _SEED_NEIGHBOURHOODS.
        assert rg.reverse_geocode(seeded, -1.288, 36.787) == "Kilimani"

    def test_karen(self, seeded):
        assert rg.reverse_geocode(seeded, -1.317, 36.707) == "Karen"

    def test_south_c(self, seeded):
        assert rg.reverse_geocode(seeded, -1.310, 36.822) == "South C"

    def test_juja(self, seeded):
        assert rg.reverse_geocode(seeded, -1.092, 37.043) == "Juja"

    def test_cbd(self, seeded):
        assert rg.reverse_geocode(seeded, -1.285, 36.825) == "Nairobi CBD"


class TestReverseGeocodeFallback:
    def test_inside_catch_all_but_outside_specific_returns_nairobi(self, seeded):
        # A point inside the metro bbox but not covered by any specific suburb. Approx
        # somewhere northwest of Kikuyu — inside the big catch-all but not inside the
        # seeded suburbs. If this coord happens to land in one, adjust; the test is about
        # the catch-all fallback path.
        assert rg.reverse_geocode(seeded, -1.400, 36.900) == "Nairobi"

    def test_outside_metro_returns_none(self, seeded):
        # Mombasa: well outside every rectangle we seeded.
        assert rg.reverse_geocode(seeded, -4.05, 39.66) is None

    def test_totally_unrelated_coord_returns_none(self, seeded):
        # Somewhere in Paris.
        assert rg.reverse_geocode(seeded, 48.8566, 2.3522) is None


class TestReverseGeocodeInvalidInput:
    def test_none_lat_returns_none(self, seeded):
        assert rg.reverse_geocode(seeded, None, 36.8) is None  # type: ignore[arg-type]

    def test_none_lng_returns_none(self, seeded):
        assert rg.reverse_geocode(seeded, -1.29, None) is None  # type: ignore[arg-type]

    def test_nan_lat_returns_none(self, seeded):
        assert rg.reverse_geocode(seeded, math.nan, 36.8) is None

    def test_out_of_range_lat_returns_none(self, seeded):
        assert rg.reverse_geocode(seeded, 91.0, 36.8) is None
        assert rg.reverse_geocode(seeded, -91.0, 36.8) is None

    def test_out_of_range_lng_returns_none(self, seeded):
        assert rg.reverse_geocode(seeded, -1.29, 181.0) is None
        assert rg.reverse_geocode(seeded, -1.29, -181.0) is None


class TestPriorityTiebreak:
    def test_specific_wins_over_catch_all(self, seeded):
        # A coord inside BOTH Kilimani AND the "nairobi" catch-all → returns "Kilimani".
        # This is the whole point of the priority tiebreak.
        result = rg.reverse_geocode(seeded, -1.288, 36.787)
        assert result == "Kilimani"
        assert result != "Nairobi"

    def test_manual_priority_ordering(self, db_session):
        # Insert two overlapping test rectangles with different priorities; the one with the
        # lower number wins.
        db_session.add_all([
            Neighbourhood(
                slug="test-small", name="Small Area",
                min_lat=-1.30, max_lat=-1.28, min_lng=36.78, max_lng=36.80, priority=5,
            ),
            Neighbourhood(
                slug="test-big", name="Big Area",
                min_lat=-1.40, max_lat=-1.20, min_lng=36.70, max_lng=36.90, priority=50,
            ),
        ])
        db_session.commit()
        # A coord in the overlap returns Small Area (lower priority number).
        assert rg.reverse_geocode(db_session, -1.29, 36.79) == "Small Area"
