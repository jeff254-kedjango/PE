import { API_BASE_URL, fetchJson } from './config';
import type {
  AgentFunnelResponse, AgentRankResponse, AnalyticsSummary, CategoryStat,
  EngagementResponse, HeatmapResponse, ListingBenchmark, PriceDistribution,
  RiskSummary, SinceWindow,
} from '../types/analytics';

function authHeaders(token: string): Record<string, string> {
  return { Authorization: `Bearer ${token}` };
}

export function fetchAnalyticsSummary(token: string, since: SinceWindow) {
  return fetchJson<AnalyticsSummary>(
    `${API_BASE_URL}/analytics/summary?since=${since}`,
    { headers: authHeaders(token), credentials: 'include' },
  );
}

export function fetchCategoryStats(token: string, since: SinceWindow) {
  return fetchJson<CategoryStat[]>(
    `${API_BASE_URL}/analytics/categories?since=${since}`,
    { headers: authHeaders(token), credentials: 'include' },
  );
}

export function fetchPriceStats(
  token: string,
  since: SinceWindow,
  listingType?: 'rent' | 'sale',
) {
  const lt = listingType ? `&listing_type=${listingType}` : '';
  return fetchJson<PriceDistribution>(
    `${API_BASE_URL}/analytics/prices?since=${since}${lt}`,
    { headers: authHeaders(token), credentials: 'include' },
  );
}

export function fetchAccessHeatmap(token: string, since: SinceWindow, county?: string) {
  const c = county ? `&county=${encodeURIComponent(county)}` : '';
  return fetchJson<HeatmapResponse>(
    `${API_BASE_URL}/analytics/heatmap/access?since=${since}${c}`,
    { headers: authHeaders(token), credentials: 'include' },
  );
}

export function fetchInterestHeatmap(token: string, since: SinceWindow, county?: string) {
  const c = county ? `&county=${encodeURIComponent(county)}` : '';
  return fetchJson<HeatmapResponse>(
    `${API_BASE_URL}/analytics/heatmap/interest?since=${since}${c}`,
    { headers: authHeaders(token), credentials: 'include' },
  );
}

export function fetchAgentRank(token: string, since: SinceWindow) {
  return fetchJson<AgentRankResponse>(
    `${API_BASE_URL}/analytics/agent/rank?since=${since}`,
    { headers: authHeaders(token), credentials: 'include' },
  );
}

export function fetchAgentFunnel(token: string, since: SinceWindow) {
  return fetchJson<AgentFunnelResponse>(
    `${API_BASE_URL}/analytics/agent/funnel?since=${since}`,
    { headers: authHeaders(token), credentials: 'include' },
  );
}

export function fetchListingBenchmarks(token: string, since: SinceWindow) {
  return fetchJson<ListingBenchmark[]>(
    `${API_BASE_URL}/analytics/agent/listings/benchmarks?since=${since}`,
    { headers: authHeaders(token), credentials: 'include' },
  );
}

/**
 * Per-role engagement series (Staff/Admin only — backend gates with require_staff).
 *
 * One request returns all three roles' series, so the Staff dashboard renders
 * three line charts from a single round-trip instead of fanning out.
 */
export function fetchEngagement(token: string, since: SinceWindow) {
  return fetchJson<EngagementResponse>(
    `${API_BASE_URL}/analytics/engagement?since=${since}`,
    { headers: authHeaders(token), credentials: 'include' },
  );
}

/**
 * Risk-oversight tile (Staff/Admin only — backend gates with require_staff).
 * Returns coverage counts + a count of active listings on a currently-unsafe
 * building. Counts only; no listing/flag rows cross the wire.
 */
export function fetchRiskSummary(token: string) {
  return fetchJson<RiskSummary>(
    `${API_BASE_URL}/analytics/risk/summary`,
    { headers: authHeaders(token), credentials: 'include' },
  );
}

export function reportSessionGeo(lat: number, lng: number) {
  return fetchJson<{ ok: boolean }>(`${API_BASE_URL}/sessions/geo`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    credentials: 'include',
    body: JSON.stringify({ lat, lng }),
  });
}
