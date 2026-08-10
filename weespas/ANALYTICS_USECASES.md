# Analytics Endpoints — Usecase Specification

Source of truth for the `/stats` dashboard charts. Derived from `routers/analytics.py`
and `services/analytics_service.py`. All endpoints are mounted under `/analytics` and
require an authenticated agent / staff / admin token (`require_agent`).

## Common parameters

| Param | Where | Format | Default | Notes |
|---|---|---|---|---|
| `since` | query | `^(\d+d|all)$` (e.g. `7d`, `30d`, `90d`, `all`) | `30d` | Filters by row `created_at` / `viewed_at`. `all` disables the cutoff. Unparseable inputs fall back to 30 days. |
| `county` | query (heatmaps only) | string ≤120 chars | none | When set, drills the heatmap from county-level aggregation to city-level within that county. |

## Engagement weights

A single weighting formula is used across endpoints to compose "interest":

```
WEIGHT_VIEW     = 1.0
WEIGHT_SEARCH   = 2.0
WEIGHT_FAVORITE = 3.0
WEIGHT_INQUIRY  = 5.0
```

Where searches cannot be linked to a single property (price histogram, interest
heatmap by Address) the search component is excluded — see notes on each endpoint.

---

## 1. `GET /analytics/summary`

High-level counters for the AnalyticsSummaryStrip.

**Response**

```json
{
  "since": "30d",
  "sessions": 302,
  "views": 3940,
  "searches": 175,
  "favorites": 6,
  "inquiries": 13
}
```

**Aggregation**

- `sessions`  → `COUNT(UserSession)` filtered by `created_at >= cutoff`
- `views`     → `COUNT(PropertyViewEvent)` filtered by `viewed_at >= cutoff`
- `searches`  → `COUNT(SearchLog)` filtered by `created_at >= cutoff`
- `favorites` → `COUNT(Favorite)` filtered by `created_at >= cutoff`
- `inquiries` → `COUNT(ContactSubmission WHERE property_id IS NOT NULL)`
  filtered by `created_at >= cutoff`

> **Why scoped to `property_id IS NOT NULL`**: keeps the figure consistent with
> per-category and per-property aggregations, which all require a property link.
> Generic site-contact submissions are excluded from "Inquiries".

---

## 2. `GET /analytics/categories`

Per-category engagement ranking. Drives `CategoryInterestChart`.

**Response** — array, sorted by `score` descending.

```json
[
  {
    "category_id": "abc",
    "slug": "apartment",
    "name": "Apartment",
    "view_count": 240,
    "search_count": 31,
    "favorite_count": 4,
    "inquiry_count": 2,
    "score": 326.0
  }
]
```

**Score formula**

```
score = view_count * 1 + search_count * 2 + favorite_count * 3 + inquiry_count * 5
```

Categories with `score == 0` are omitted from the response.

---

## 3. `GET /analytics/prices`

Histogram of property prices weighted by engagement. Drives `PriceRangeChart`.

**Query**: `listing_type` (`rent` | `sale`, optional) limits the engagement scan to one type.

**Response**

```json
{
  "since": "30d",
  "listing_type": "sale",
  "sale": [{"bucket": "0-5,000,000", "score": 12.0}, ...],
  "rent": [{"bucket": "0-25,000",    "score": 4.0}, ...]
}
```

**Buckets**

```
SALE  KES: 0–5M, 5–10M, 10–25M, 25–50M, 50–100M, 100M+
RENT  KES: 0–25k, 25–50k, 50–100k, 100–200k, 200–500k, 500k+
```

**Score formula** (per property, then summed into bucket of `Property.price`)

```
engagement = views * 1 + favorites * 3 + inquiries * 5
```

> **Why no `searches`**: `SearchLog` rows are filters/queries, not associated
> with a specific property — they cannot be attributed to a single price bucket.
> The chart subtitle reflects this.

Properties with `engagement == 0` and `Property.price IS NULL` are skipped.
Both `sale` and `rent` arrays are always returned (frontend can tab between them
without re-fetching).

---

## 4. `GET /analytics/heatmap/access`

Where the audience accesses the app from (geo-IP on `UserSession`). Drives the
"Where users are accessing from" map.

**Response**

```json
{
  "level": "county",
  "county": null,
  "points": [
    { "name": "Nairobi", "lat": -1.29, "lng": 36.82, "weight": 26 }
  ]
}
```

- `level == "county"` when `county` query param is omitted; group by `geo_county`.
- `level == "city"`   when `county` is set; group by `geo_city WHERE geo_county = ?`.
- `weight` is the count of sessions in that bucket.
- Sessions with NULL `geo_lat` / `geo_lng` are excluded.

---

## 5. `GET /analytics/heatmap/interest`

Where users are *looking* — engagement on properties in each county / city.
Drives the "Where users are looking" map.

**Response** — same envelope as access.

**Aggregation**

- Joins `Property → Address` and outer-joins per-property view / fav / inquiry counts.
- `weight = SUM(views * 1 + favorites * 3 + inquiries * 5)` per group.
- Group-by: `Address.county` (county level) or `Address.city` (city level when
  `county` filter is set).
- Buckets with `weight <= 0` or null lat/lng are dropped.

**Raw search points**

At county level, the response is augmented with raw points from `SearchLog`
records that carry `latitude` + `longitude`, with `name: null` and `weight: 2.0`
each. These represent search interest the heatmap renders without bucketing.

> **Why dropped at city level**: searches are not reverse-geocoded, so when the
> client drills into a county we cannot scope these points to the chosen county.
> They are omitted to prevent visual pollution of the city-level view.

---

## Seeding for demo

Analytics data is **not** populated by the baseline `seed.py`. To get every
chart on `/stats` to render with realistic data, run scripts in this order
inside the backend venv:

```bash
python add_analytics.py          # one-off: extends contact_submissions, creates analytics tables
python seed.py                   # baseline categories / agents / properties
python seed_expanded.py          # +7 agents +100 properties (skip if already run)
python seed_stats.py             # populates Property.view_count + agent users
python seed_analytics_edges.py   # UserSession / PropertyViewEvent / SearchLog / Favorite + geo + ContactSubmission
```

`seed_analytics_edges.py` is idempotent — it tags rows with deterministic
markers and skips on re-run. Re-running `seed_stats.py` updates `view_count` in
place. `seed_expanded.py` is **not** idempotent on agent creation.

## Caching

There is no Redis cache in front of these aggregations yet — every request
hits the DB. The `staleTime` and `gcTime` on the React Query hooks
(`useAnalyticsSummary` etc.) provide the only client-side caching. The
public function signatures in `services/analytics_service.py` are stable, so
a Redis layer can be added later without touching the router or frontend.
