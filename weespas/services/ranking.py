from dataclasses import dataclass
from math import log1p

@dataclass
class Item:
    id: int
    distance_km: float
    clicks: int
    views: int
    relevance_score: float  # 0.0..1.0

def engagement_score(clicks: int, views: int) -> float:
    # NOTE: the featured ranker currently passes clicks=0 (no click tracking yet),
    # so this term is inert there until click events are wired — kept at a small
    # weight so enabling it later is a one-line change at the call site.
    if views <= 0:
        return 0.0
    ctr = clicks / views
    return min(1.0, log1p(views) * ctr)


def trust_signal(prop, *, monitored_ids=None) -> float:
    """Safety/anti-scam relevance for a listing, in [0,1] — Weespas's differentiator.

    Feeds the `relevance_score` slot of `rank_score` so featured promotion leads with
    trustworthy listings. Reads only fields eager-loaded by the list query (no N+1):
      - engineer-certified construction  → 0.55 (strongest signal)
      - verified agent                   → 0.35
      - InSAR-monitored (link row exists)→ 0.10 (only when monitored_ids is provided)
    No signal → 0.0: the listing still appears, but proximity/recency decide its rank.

    `monitored_ids` is an optional precomputed set of listing ids that have an InSAR
    BuildingLink; pass None (default) to skip the InSAR contribution entirely (keeps
    callers that don't have the set — and tests — simple).
    """
    s = 0.0
    if getattr(prop, "is_engineer_certified", False):
        s += 0.55
    agent = getattr(prop, "agent", None)
    if agent is not None and getattr(agent, "is_verified", False):
        s += 0.35
    if monitored_ids is not None and prop.id in monitored_ids:
        s += 0.10
    return min(1.0, s)

def proximity_score(distance_km: float) -> float:
    return 1.0 / (1.0 + distance_km)

def rank_score(item: Item,
               w_d: float = 0.5,
               w_e: float = 0.3,
               w_r: float = 0.2) -> float:
    return (
        w_d * proximity_score(item.distance_km) +
        w_e * engagement_score(item.clicks, item.views) +
        w_r * item.relevance_score
    )

def rank_items(items: list[Item]) -> list[Item]:
    return sorted(items, key=rank_score, reverse=True)