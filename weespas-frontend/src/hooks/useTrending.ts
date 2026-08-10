// Trending rail hook — polls the boosted-shops slate for the buyer's locality (§8).
//
// The queue is server-deterministic per locality bucket, Redis-cached server-side with TTL =
// `poll_seconds`. So the client polls cheaply: it re-fetches at the slate's `poll_seconds` cadence
// (not a blind fixed interval) to refresh the queue membership — the per-slot decay animation is
// client-local (useTrendingRotation). This is the polling-over-SSE decision (the queue is a slow,
// predictable schedule, not an unpredictable event stream, so push would be wasted infra).
//
// `enabled` gates on a live session + coords, so it never fires a 401 before the commerce session
// exists. Failures are swallowed by the caller (the rail just hides) — it's a discovery surface.
import { useQuery } from '@tanstack/react-query';
import { getTrending, type CommerceSession, type TrendingSlate } from '../api/commerce';

// Coordinate rounding for the query key so tiny GPS jitter doesn't thrash the cache (the SERVER
// buckets to ~1.5 km anyway; ~3dp ≈ 110 m is well within one bucket, keeping the key stable).
function keyCoord(n: number): number {
  return Math.round(n * 1000) / 1000;
}

// Clamp the poll cadence to a sane floor in case the server ever returns a tiny/zero poll value, so
// a misbehaving response can't spin the client into a hot loop.
const MIN_POLL_MS = 3_000;

export function useTrending(session: CommerceSession | null, lat: number, lng: number) {
  return useQuery<TrendingSlate, Error>({
    queryKey: ['commerce', 'trending', session?.commerce_url, keyCoord(lat), keyCoord(lng)],
    queryFn: () => getTrending(session!, lat, lng),
    enabled: !!session,
    // Re-poll at the slate's own cadence so the client refreshes the queue membership. React Query
    // passes the latest query (v5) — read its data's poll_seconds; fall back to a short default pre-load.
    refetchInterval: (query) => {
      const poll = query.state.data?.poll_seconds;
      return Math.max(MIN_POLL_MS, (poll ?? 20) * 1000);
    },
    refetchOnWindowFocus: true,
    // The queue is cheap + time-sensitive; treat it as always-stale so a new boost isn't masked.
    staleTime: 0,
    retry: 1,
  });
}
