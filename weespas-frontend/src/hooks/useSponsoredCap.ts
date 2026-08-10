// Per-shop sponsored-cap override hooks (§8.3 item 1) — the seller status/apply pair and the staff
// review-queue read/decide pair.
//
// Reads use react-query (keyed by commerce_url so a redeploy to a new base gets its own entry); the
// status read is NON-DESTRUCTIVE (a plain GET) so opening the seller control can never reset an
// approved override. Mutations invalidate their matching read key so the UI reflects the new state
// without a manual refetch. Hooks stay UI-agnostic (no toasts) — components surface success/error.
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  getSponsoredCapStatus,
  applySponsoredCap,
  listPendingSponsoredCaps,
  decideSponsoredCap,
  type CommerceSession,
  type CapOverrideOut,
  type CapOverrideStatusOut,
  type PendingCapListOut,
} from '../api/commerce';

const SPONSORED_CAP_KEY = ['commerce', 'sponsored-cap'] as const;
const PENDING_CAPS_KEY = ['commerce', 'sponsored-cap', 'pending'] as const;

/** The caller's own shop's cap-override status (non-destructive). Disabled until a session + shop. */
export function useSponsoredCapStatus(session: CommerceSession | null, shopId: string | null) {
  return useQuery<CapOverrideStatusOut, Error>({
    queryKey: [...SPONSORED_CAP_KEY, session?.commerce_url, shopId],
    queryFn: () => getSponsoredCapStatus(session!, shopId!),
    enabled: !!session && !!shopId,
    staleTime: 30_000, // status changes only on the seller's own apply or a staff decision
    retry: 1,
  });
}

/** Apply for a per-shop sponsored cap; on success refresh that shop's status. */
export function useApplySponsoredCap(session: CommerceSession | null, shopId: string | null) {
  const qc = useQueryClient();
  return useMutation<CapOverrideOut, Error, number>({
    mutationFn: (requestedCap) => applySponsoredCap(session!, shopId!, requestedCap),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: [...SPONSORED_CAP_KEY, session?.commerce_url, shopId] });
    },
  });
}

/** Staff: the pending cap applications (+ server ceiling). Disabled until a session exists. */
export function usePendingSponsoredCaps(session: CommerceSession | null) {
  return useQuery<PendingCapListOut, Error>({
    queryKey: [...PENDING_CAPS_KEY, session?.commerce_url],
    queryFn: () => listPendingSponsoredCaps(session!),
    enabled: !!session,
    staleTime: 15_000,
    retry: 1,
  });
}

/** Staff: approve/reject a pending application; on success refresh the pending queue. */
export function useDecideSponsoredCap(session: CommerceSession | null) {
  const qc = useQueryClient();
  return useMutation<
    CapOverrideOut, Error, { overrideId: string; approve: boolean; approvedCap?: number | null }
  >({
    mutationFn: ({ overrideId, approve, approvedCap }) =>
      decideSponsoredCap(session!, overrideId, { approve, approvedCap }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: [...PENDING_CAPS_KEY, session?.commerce_url] });
    },
  });
}
