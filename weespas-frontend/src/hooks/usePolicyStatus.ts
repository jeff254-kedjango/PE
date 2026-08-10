// usePolicyStatus — the signed-in user's §8 commercial-use verdict, for the soft-gate.
//
// Backed by React Query so the (rarely-changing) verdict is fetched once per stale
// window and shared across subscribers. The verdict only moves when the policy beat
// job recomputes profiles (every ~30min, services/policy_tasks.py), so a 30-min
// staleTime matches the data's real cadence — no point polling faster.
//
// Gated on `token`: anonymous users never query (they're free by definition and the
// backend would 401 the Bearer-less call anyway).
import { useQuery } from '@tanstack/react-query';
import { fetchPolicyStatus, type PolicyStatus } from '../api/policy';
import { useAuth } from '../context/AuthContext';

export const POLICY_QUERY_KEY = ['policy', 'me'] as const;

export function usePolicyStatus() {
  const { token } = useAuth();

  return useQuery<PolicyStatus, Error>({
    queryKey: POLICY_QUERY_KEY,
    queryFn: () => fetchPolicyStatus(token!),
    enabled: !!token,
    staleTime: 30 * 60 * 1000,   // matches the recompute beat cadence
    gcTime: 60 * 60 * 1000,
    retry: 1,
  });
}
