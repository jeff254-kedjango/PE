// CommentThread — the public comment thread under a post (§8 social feed).
//
// Mounted ONLY when the user opens comments on a card (the parent gates rendering), and the query
// is `enabled` so a feed of N posts never fires N comment fetches on render. Shows the thread
// newest-first with a "Show more" keyset pager and an inline composer. The composer is optimistic-
// free (it waits for the server, which is the authority on trimming/length 422) but disables while
// posting and clears on success — the post hook invalidates the thread + the feed comment_count.
import React, { useRef, useState } from 'react';
import {
  useListingComments, usePostComment, useToggleCommentLike,
} from '../../hooks/useListingComments';
import { COMMENT_MAX_LEN, displayName, type Comment, type CommerceSession } from '../../api/commerce';
import { insertAtCursor } from '../../utils/insertAtCursor';
import useHeartPop from '../../hooks/useHeartPop';
import Icon from '../ui/Icon';
import EmojiPalette from './EmojiPalette';
import './CommentThread.css';

interface CommentThreadProps {
  session: CommerceSession | null;
  listingId: string;
}

// A single comment row, split out so the like button's pending state is local (one comment's like
// in flight never disables the others).
const CommentRow: React.FC<{
  comment: Comment;
  session: CommerceSession | null;
  listingId: string;
}> = ({ comment, session, listingId }) => {
  const author = displayName(comment.author_name);
  const toggleLike = useToggleCommentLike(session, listingId);
  const liked = comment.liked_by_me;
  const { popping, pop } = useHeartPop();
  const handleLike = () => {
    if (!session || toggleLike.isPending) return;
    pop();
    toggleLike.mutate(comment.id);
  };
  return (
    <li className="comment-thread__item">
      <span className="comment-thread__avatar" aria-hidden="true">
        {author.slice(0, 1).toUpperCase()}
      </span>
      <div className="comment-thread__bubble">
        <span className="comment-thread__author">{author}</span>
        <p className="comment-thread__body">{comment.body}</p>
        <button
          type="button"
          className={`comment-thread__like${liked ? ' comment-thread__like--on' : ''}`}
          onClick={handleLike}
          disabled={!session || toggleLike.isPending}
          aria-pressed={liked}
          aria-label={liked ? 'Unlike comment' : 'Like comment'}
          data-testid="comment-like"
        >
          <Icon name={liked ? 'heartFilled' : 'heart'} size={14} className={popping ? 'animate-heart' : ''} />
          {comment.like_count > 0 && (
            <span className="comment-thread__like-count">{comment.like_count}</span>
          )}
        </button>
      </div>
    </li>
  );
};

const CommentThread: React.FC<CommentThreadProps> = ({ session, listingId }) => {
  const [draft, setDraft] = useState('');
  const inputRef = useRef<HTMLInputElement>(null);
  const emojiBtnRef = useRef<HTMLButtonElement>(null);
  const [showEmoji, setShowEmoji] = useState(false);
  const {
    comments, isLoading, isError, fetchNextPage, hasNextPage, isFetchingNextPage,
  } = useListingComments(session, listingId, true);
  const postComment = usePostComment(session, listingId);

  const trimmed = draft.trim();
  const canSend = trimmed.length > 0 && trimmed.length <= COMMENT_MAX_LEN && !postComment.isPending;

  const insertEmoji = (emoji: string) => {
    const el = inputRef.current;
    const { next, caret } = insertAtCursor(el, draft, emoji);
    setDraft(next);
    setShowEmoji(false);
    requestAnimationFrame(() => {
      if (el) { el.focus(); el.setSelectionRange(caret, caret); }
    });
  };

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!canSend) return;
    postComment.mutate(trimmed, { onSuccess: () => setDraft('') });
  };

  return (
    <div className="comment-thread" data-testid="comment-thread">
      <form className="comment-thread__composer" onSubmit={submit}>
        <div className="comment-thread__input-wrap">
          <input
            ref={inputRef}
            type="text"
            className="comment-thread__input"
            placeholder="Add a public comment…"
            value={draft}
            maxLength={COMMENT_MAX_LEN}
            onChange={(e) => setDraft(e.target.value)}
            aria-label="Add a public comment"
            disabled={postComment.isPending}
          />
          <button
            ref={emojiBtnRef}
            type="button"
            className="comment-thread__emoji-btn"
            onClick={() => setShowEmoji((s) => !s)}
            disabled={postComment.isPending}
            aria-label="Add emoji"
            data-testid="comment-emoji"
          >
            😊
          </button>
          {showEmoji && (
            <EmojiPalette onPick={insertEmoji} onClose={() => setShowEmoji(false)} anchorRef={emojiBtnRef} />
          )}
        </div>
        <button type="submit" className="comment-thread__send" disabled={!canSend}>
          {postComment.isPending ? '…' : 'Post'}
        </button>
      </form>

      {postComment.isError && (
        <p className="comment-thread__error" role="alert">Couldn’t post your comment. Try again.</p>
      )}

      {isLoading && <p className="comment-thread__state">Loading comments…</p>}
      {isError && <p className="comment-thread__state" role="alert">Couldn’t load comments.</p>}
      {!isLoading && !isError && comments.length === 0 && (
        <p className="comment-thread__state">No comments yet — be the first.</p>
      )}

      <ul className="comment-thread__list">
        {comments.map((c) => (
          <CommentRow key={c.id} comment={c} session={session} listingId={listingId} />
        ))}
      </ul>

      {hasNextPage && (
        <button
          type="button"
          className="comment-thread__more"
          onClick={() => fetchNextPage()}
          disabled={isFetchingNextPage}
        >
          {isFetchingNextPage ? 'Loading…' : 'Show more comments'}
        </button>
      )}
    </div>
  );
};

export default CommentThread;
