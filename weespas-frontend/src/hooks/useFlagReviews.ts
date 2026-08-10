// Flag-review queue hooks (the staff/admin badge + queue list).
//
// Mirrors useNotifications: a 60s poll on the open-count (a single indexed COUNT) and a
// lazily-fetched list. Every hook is gated on isStaffOrAdmin(user) AND a token, so a
// normal user's navbar never fires a 403 against these staff-only endpoints.
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { useAuth } from '../context/AuthContext';
import { isStaffOrAdmin } from '../utils/roles';
import {
  fetchFlagReviews,
  fetchOpenFlagReviewCount,
  markFlagReviewSeen,
  recordFlagReviewView,
  type FlagReview,
  type OpenFlagReviewCount,
} from '../api/flagReviews';

const OPEN_COUNT_KEY = ['flag-reviews', 'open-count'];
const LIST_KEY = ['flag-reviews', 'list'];

/** Open-flag badge for staff/admin. Polls every 60s; disabled for everyone else so it
 *  never fires a 403 on a non-staff navbar mount. */
export function useOpenFlagReviewCount() {
  const { token, user, isAuthenticated } = useAuth();
  const enabled = !!token && isAuthenticated && isStaffOrAdmin(user);
  return useQuery<OpenFlagReviewCount, Error>({
    queryKey: OPEN_COUNT_KEY,
    queryFn: () => fetchOpenFlagReviewCount(token!),
    enabled,
    staleTime: 60_000,
    refetchInterval: 60_000,
    refetchOnWindowFocus: true,
    retry: 1,
  });
}

/** The review list. `enabled` lets the caller fetch only when the queue is shown. */
export function useFlagReviewList(
  enabled: boolean,
  status: 'open' | 'all' = 'open',
) {
  const { token, user, isAuthenticated } = useAuth();
  return useQuery<FlagReview[], Error>({
    queryKey: [...LIST_KEY, status],
    queryFn: () => fetchFlagReviews(token!, { status, limit: 50 }),
    enabled: !!token && isAuthenticated && isStaffOrAdmin(user) && enabled,
    staleTime: 30_000,
    retry: 1,
  });
}

/** Mark one review seen, then refresh the badge + list. */
export function useMarkFlagReviewSeen() {
  const { token } = useAuth();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => markFlagReviewSeen(token!, id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: OPEN_COUNT_KEY });
      qc.invalidateQueries({ queryKey: LIST_KEY });
    },
  });
}

/** Record a distinct view, then refresh the list (the view count is shown there). */
export function useRecordFlagReviewView() {
  const { token } = useAuth();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => recordFlagReviewView(token!, id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: LIST_KEY });
    },
  });
}
