// Public-comment hooks for a listing post (§8 social thread).
//
// `useListingComments` is a keyset infinite query over the listing's PUBLIC thread (newest-first,
// id-anchored cursor — same shape as the feed). `usePostComment` posts a comment and invalidates
// BOTH the thread (so the new comment appears) and the feed (so the post's `comment_count`
// display badge refreshes). The thread is only fetched when `enabled` is true — i.e. when the
// user actually opens the comments on a card, so a feed of N posts doesn't fire N comment
// requests on render (no N+1).
import {
  useInfiniteQuery,
  useMutation,
  useQueryClient,
  type InfiniteData,
} from '@tanstack/react-query';
import {
  listComments,
  postComment,
  toggleCommentLike,
  type Comment,
  type CommentLikeToggle,
  type CommentPage,
  type CommerceSession,
} from '../api/commerce';

export function useListingComments(
  session: CommerceSession | null,
  listingId: string,
  enabled: boolean,
) {
  const query = useInfiniteQuery<
    CommentPage,
    Error,
    InfiniteData<CommentPage, string | null>,
    readonly unknown[],
    string | null
  >({
    queryKey: ['commerce', 'comments', session?.commerce_url, listingId],
    queryFn: ({ pageParam }) =>
      listComments(session!, listingId, { cursor: pageParam }),
    getNextPageParam: (lastPage) => lastPage.next_cursor ?? undefined,
    initialPageParam: null,
    enabled: !!session && enabled,
    staleTime: 30_000,
    refetchOnWindowFocus: false,
    retry: 1,
  });

  const comments: Comment[] = query.data?.pages.flatMap((p) => p.items) ?? [];
  return { ...query, comments };
}

export function usePostComment(session: CommerceSession | null, listingId: string) {
  const qc = useQueryClient();
  return useMutation<Comment, Error, string>({
    mutationFn: (body: string) => postComment(session!, listingId, body),
    onSuccess: () => {
      // Refresh the open thread and the feed's comment_count badge.
      qc.invalidateQueries({ queryKey: ['commerce', 'comments', session?.commerce_url, listingId] });
      qc.invalidateQueries({ queryKey: ['commerce', 'feed', session?.commerce_url] });
    },
  });
}

/** Toggle a like ("love") on a comment. Invalidates the open thread so the heart + count re-read
 *  from the server (the authoritative count, incl. other users' likes). `listingId` keys the same
 *  thread query the comment belongs to. */
export function useToggleCommentLike(session: CommerceSession | null, listingId: string) {
  const qc = useQueryClient();
  return useMutation<CommentLikeToggle, Error, string>({
    mutationFn: (commentId: string) => toggleCommentLike(session!, commentId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['commerce', 'comments', session?.commerce_url, listingId] });
    },
  });
}
