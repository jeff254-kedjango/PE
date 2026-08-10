"""Run once after a fresh deploy / Redis flush.

Schedules every analytics aggregator with both supported `since` values so
the cache is hot before user traffic arrives. Without this, the first 10
minutes after a cold start serve from live-compute (SWR fallback) — slower
than the steady-state Redis-only path.

Usage:
    python scripts/warm_analytics.py
"""
from __future__ import annotations

import sys
from PE.weespas.services import analytics_tasks
from PE.weespas.services.celery_helpers import safe_delay


SINCE_VALUES = ["30d", "all"]


def main() -> int:
    dispatched = 0

    for since in SINCE_VALUES:
        safe_delay(analytics_tasks.aggregate_summary, since);     dispatched += 1
        safe_delay(analytics_tasks.aggregate_categories, since);  dispatched += 1
        safe_delay(analytics_tasks.aggregate_prices, since);      dispatched += 1
        safe_delay(analytics_tasks.aggregate_heatmaps, since);    dispatched += 1
        safe_delay(analytics_tasks.compute_engagement, since);    dispatched += 1
        safe_delay(analytics_tasks.compute_agent_rank, since);    dispatched += 1
        safe_delay(analytics_tasks.compute_agent_funnel, since);  dispatched += 1

    # Listing benchmarks + prop counts are not since-parameterized at warm time.
    safe_delay(analytics_tasks.compute_listing_benchmarks, "30d"); dispatched += 1
    safe_delay(analytics_tasks.refresh_agent_prop_counts);          dispatched += 1

    print(f"Scheduled {dispatched} warm tasks. Watch Flower for completion.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
