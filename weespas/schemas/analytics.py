"""Pydantic response models for the analytics endpoints.

Currently only the agent-comparison endpoints (rank, funnel, listing benchmarks)
have explicit schemas. The older heatmap / summary endpoints return plain dicts
because their shapes are trivial and stable.
"""
from __future__ import annotations

from typing import List, Optional
from pydantic import BaseModel


class AgentRankSelf(BaseModel):
    id: str
    name: str
    rank: int
    total: int
    percentile: float
    score: float
    engagement_per_listing: float
    active_listings: int


class AgentRankPlatform(BaseModel):
    p50: float
    p90: float


class LeaderboardRow(BaseModel):
    rank: int
    agent_id: str
    name: str
    score: float
    engagement_per_listing: float
    active_listings: int
    is_me: bool = False


class AgentRankResponse(BaseModel):
    since: str
    agent: Optional[AgentRankSelf] = None
    platform: AgentRankPlatform
    leaderboard: List[LeaderboardRow]


class FunnelSide(BaseModel):
    views: int
    favorites: int
    inquiries: int
    view_to_fav: Optional[float] = None
    fav_to_inq: Optional[float] = None


class FunnelPlatform(BaseModel):
    view_to_fav: Optional[float] = None
    fav_to_inq: Optional[float] = None


class AgentFunnelResponse(BaseModel):
    since: str
    agent: Optional[FunnelSide] = None
    platform: FunnelPlatform


class ListingBenchmark(BaseModel):
    property_id: str
    title: str
    score: float
    views: int
    favorites: int
    inquiries: int
    peer_set: str  # 'category_county_type' | 'category_type' | 'insufficient'
    peer_count: int
    peer_median_views: Optional[float] = None
    peer_p90_views: Optional[float] = None
    percentile: Optional[float] = None
