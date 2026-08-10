export type SinceWindow = '7d' | '30d' | '90d' | 'all';

export interface AnalyticsSummary {
  since: string;
  sessions: number;
  views: number;
  searches: number;
  favorites: number;
  inquiries: number;
}

// Risk-oversight tile (staff/admin). Counts only — never the underlying rows.
export interface RiskSummary {
  coverage: {
    monitored: number;
    not_monitored: number;
    pending: number;
    unavailable: number;
  };
  monitored: number;
  not_monitored: number;
  pending: number;
  unavailable: number;
  unsafe_listings: number;   // active listings on a currently-unsafe building
}

export interface CategoryStat {
  category_id: string;
  slug: string;
  name: string;
  view_count: number;
  search_count: number;
  favorite_count: number;
  inquiry_count: number;
  score: number;
}

export interface PriceBucket {
  bucket: string;
  score: number;
}

export interface PriceDistribution {
  since: string;
  listing_type: string | null;
  sale: PriceBucket[];
  rent: PriceBucket[];
}

export interface HeatmapPoint {
  name: string;
  lat: number;
  lng: number;
  weight: number;
}

export interface HeatmapResponse {
  level: 'county' | 'city';
  county: string | null;
  points: HeatmapPoint[];
}

export interface LeaderboardRow {
  rank: number;
  agent_id: string;
  name: string;
  score: number;
  engagement_per_listing: number;
  active_listings: number;
  is_me: boolean;
}

export interface AgentRankSelf {
  id: string;
  name: string;
  rank: number;
  total: number;
  percentile: number;
  score: number;
  engagement_per_listing: number;
  active_listings: number;
}

export interface AgentRankResponse {
  since: string;
  agent: AgentRankSelf | null;
  platform: { p50: number; p90: number };
  leaderboard: LeaderboardRow[];
}

export interface FunnelSide {
  views: number;
  favorites: number;
  inquiries: number;
  view_to_fav: number | null;
  fav_to_inq: number | null;
}

export interface AgentFunnelResponse {
  since: string;
  agent: FunnelSide | null;
  platform: { view_to_fav: number | null; fav_to_inq: number | null };
}

export type BenchmarkPeerSet = 'category_county_type' | 'category_type' | 'insufficient';

export interface ListingBenchmark {
  property_id: string;
  title: string;
  score: number;
  views: number;
  favorites: number;
  inquiries: number;
  peer_set: BenchmarkPeerSet;
  peer_count: number;
  peer_median_views: number | null;
  peer_p90_views: number | null;
  percentile: number | null;
}

/**
 * One day in the engagement time series for a single role.
 *
 * `return_interval_hours`  — avg of per-user MEDIAN gap between consecutive
 *                            sessions that day. Median (not mean) per user so
 *                            one tourist session doesn't drag the whole role.
 * `avg_usage_minutes`      — avg session duration (last_seen_at - created_at)
 *                            across all sessions that day.
 * Both may be `null` on days where no sessions exist for that role.
 */
export interface EngagementPoint {
  date: string;
  return_interval_hours: number | null;
  avg_usage_minutes: number | null;
}

export type EngagementRole = 'user' | 'agent' | 'staff';

export interface EngagementRoleSeries {
  series: EngagementPoint[];
}

export interface EngagementResponse {
  since: string;
  roles: Record<EngagementRole, EngagementRoleSeries>;
}

