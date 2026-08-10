// Shop profile hovercard hooks (§8) — a shop's published business card + follow ("Notify").
//
// useShopProfile is LAZY: it only fetches when `enabled` (the card is open) so a feed of N posts
// never fires N profile requests on render — the request goes out the first time a buyer opens a
// shop's hovercard, then is cached per (commerce base, shopId).
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  getShopProfile, toggleShopFollow,
  type CommerceSession, type ShopProfile, type FollowToggle,
} from '../api/commerce';

function profileKey(session: CommerceSession | null, shopId: string) {
  return ['commerce', 'shopProfile', session?.commerce_url, shopId] as const;
}

export function useShopProfile(session: CommerceSession | null, shopId: string, enabled: boolean) {
  return useQuery<ShopProfile, Error>({
    queryKey: profileKey(session, shopId),
    queryFn: () => getShopProfile(session!, shopId),
    enabled: !!session && !!shopId && enabled,
    staleTime: 60_000,
    retry: 1,
  });
}

/** Toggle follow on a shop, with an optimistic flip + rollback on failure.
 *
 *  The storefront (a page the buyer stays on) shows the button state directly, so a mid-network
 *  hover-then-click has to feel instant. We optimistically flip `following` and bump
 *  `follower_count` by ±1 in the cached profile the moment the mutation starts; if the server
 *  rejects, we restore the pre-flip snapshot in `onError`. On success we still write the server's
 *  authoritative numbers over the optimistic ones — the server counts don't necessarily equal
 *  `prev ± 1` (a stale profile may have missed intervening follows/unfollows). Uses the standard
 *  React Query onMutate → onError → onSuccess (or the older onSuccess-only path if the caller
 *  ignores rollback) pattern; the hovercard reads the same cache, so BOTH surfaces get the
 *  optimism transparently. */
export function useToggleShopFollow(session: CommerceSession | null, shopId: string) {
  const qc = useQueryClient();
  return useMutation<FollowToggle, Error, void, { prev: ShopProfile | undefined }>({
    mutationFn: () => toggleShopFollow(session!, shopId),
    onMutate: async () => {
      // Cancel any inflight profile fetch so its response can't overwrite our optimistic flip.
      await qc.cancelQueries({ queryKey: profileKey(session, shopId) });
      const prev = qc.getQueryData<ShopProfile>(profileKey(session, shopId));
      if (prev) {
        qc.setQueryData<ShopProfile>(profileKey(session, shopId), {
          ...prev,
          following: !prev.following,
          // Bump/decrement by 1; clamp at 0 defensively so a stale "already unfollowed" cache
          // doesn't produce a negative count in the UI.
          follower_count: Math.max(0, prev.follower_count + (prev.following ? -1 : 1)),
        });
      }
      return { prev };
    },
    onError: (_err, _vars, ctx) => {
      // Roll back to the snapshot taken at onMutate.
      if (ctx?.prev !== undefined) {
        qc.setQueryData<ShopProfile>(profileKey(session, shopId), ctx.prev);
      }
    },
    onSuccess: (result) => {
      // Server truth wins — overwrite the optimistic numbers (they usually agree, but the server
      // is authoritative on both count and following state).
      qc.setQueryData<ShopProfile>(profileKey(session, shopId), (prev) =>
        prev
          ? { ...prev, following: result.following, follower_count: result.follower_count }
          : prev,
      );
    },
  });
}
