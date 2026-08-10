import { useQuery } from '@tanstack/react-query';
import {
  fetchAccessHeatmap, fetchAgentFunnel, fetchAgentRank, fetchAnalyticsSummary,
  fetchCategoryStats, fetchEngagement, fetchInterestHeatmap, fetchListingBenchmarks,
  fetchPriceStats, fetchRiskSummary,
} from '../api/analytics';
import type {
  AgentFunnelResponse, AgentRankResponse, AnalyticsSummary, CategoryStat,
  EngagementResponse, HeatmapResponse, ListingBenchmark, PriceDistribution,
  RiskSummary, SinceWindow,
} from '../types/analytics';

const baseOpts = {
  staleTime: 1000 * 60 * 2,
  gcTime: 1000 * 60 * 15,
  refetchOnWindowFocus: false,
  retry: 1,
};

export function useAnalyticsSummary(token: string | null, since: SinceWindow) {
  return useQuery<AnalyticsSummary, Error>({
    queryKey: ['analytics', 'summary', since, token ?? 'anon'],
    queryFn: () => {
      if (!token) throw new Error('Token required');
      return fetchAnalyticsSummary(token, since);
    },
    enabled: Boolean(token),
    ...baseOpts,
  });
}

export function useCategoryStats(token: string | null, since: SinceWindow) {
  return useQuery<CategoryStat[], Error>({
    queryKey: ['analytics', 'categories', since, token ?? 'anon'],
    queryFn: () => {
      if (!token) throw new Error('Token required');
      return fetchCategoryStats(token, since);
    },
    enabled: Boolean(token),
    ...baseOpts,
  });
}

export function useAccessHeatmap(token: string | null, since: SinceWindow, county?: string) {
  return useQuery<HeatmapResponse, Error>({
    queryKey: ['analytics', 'heatmap', 'access', since, county ?? 'all', token ?? 'anon'],
    queryFn: () => {
      if (!token) throw new Error('Token required');
      return fetchAccessHeatmap(token, since, county);
    },
    enabled: Boolean(token),
    ...baseOpts,
  });
}

export function useInterestHeatmap(token: string | null, since: SinceWindow, county?: string) {
  return useQuery<HeatmapResponse, Error>({
    queryKey: ['analytics', 'heatmap', 'interest', since, county ?? 'all', token ?? 'anon'],
    queryFn: () => {
      if (!token) throw new Error('Token required');
      return fetchInterestHeatmap(token, since, county);
    },
    enabled: Boolean(token),
    ...baseOpts,
  });
}

export function useAgentRank(token: string | null, since: SinceWindow) {
  return useQuery<AgentRankResponse, Error>({
    queryKey: ['analytics', 'agent-rank', since, token ?? 'anon'],
    queryFn: () => {
      if (!token) throw new Error('Token required');
      return fetchAgentRank(token, since);
    },
    enabled: Boolean(token),
    ...baseOpts,
  });
}

export function useAgentFunnel(token: string | null, since: SinceWindow) {
  return useQuery<AgentFunnelResponse, Error>({
    queryKey: ['analytics', 'agent-funnel', since, token ?? 'anon'],
    queryFn: () => {
      if (!token) throw new Error('Token required');
      return fetchAgentFunnel(token, since);
    },
    enabled: Boolean(token),
    ...baseOpts,
  });
}

export function useListingBenchmarks(token: string | null, since: SinceWindow) {
  return useQuery<ListingBenchmark[], Error>({
    queryKey: ['analytics', 'listing-benchmarks', since, token ?? 'anon'],
    queryFn: () => {
      if (!token) throw new Error('Token required');
      return fetchListingBenchmarks(token, since);
    },
    enabled: Boolean(token),
    ...baseOpts,
  });
}

/**
 * Single-shot fetcher for the three-role engagement series shown on the
 * Staff dashboard. Staff/Admin only — the backend returns 403 for everyone
 * else, so callers should already be inside a role-gated page.
 */
export function useEngagement(token: string | null, since: SinceWindow) {
  return useQuery<EngagementResponse, Error>({
    queryKey: ['analytics', 'engagement', since, token ?? 'anon'],
    queryFn: () => {
      if (!token) throw new Error('Token required');
      return fetchEngagement(token, since);
    },
    enabled: Boolean(token),
    ...baseOpts,
  });
}

export function usePriceStats(
  token: string | null,
  since: SinceWindow,
  listingType?: 'rent' | 'sale',
) {
  return useQuery<PriceDistribution, Error>({
    queryKey: ['analytics', 'prices', since, listingType ?? 'all', token ?? 'anon'],
    queryFn: () => {
      if (!token) throw new Error('Token required');
      return fetchPriceStats(token, since, listingType);
    },
    enabled: Boolean(token),
    ...baseOpts,
  });
}

export function useRiskSummary(token: string | null) {
  return useQuery<RiskSummary, Error>({
    queryKey: ['analytics', 'risk-summary', token ?? 'anon'],
    queryFn: () => {
      if (!token) throw new Error('Token required');
      return fetchRiskSummary(token);
    },
    enabled: Boolean(token),
    ...baseOpts,
  });
}

