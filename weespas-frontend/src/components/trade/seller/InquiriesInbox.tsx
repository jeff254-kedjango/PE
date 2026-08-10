// InquiriesInbox — the seller's "is this available?" inbox (§8 social engagement, seller side).
//
// Newest-first list of buyer inquiries on the seller's listings. Unread items are visually marked;
// "Mark read" clears that (recipient-only + idempotent server-side). The unread count surfaces as a
// badge on the console's Inbox header (computed from the same list — no extra request).
import React from 'react';
import { useToast } from '../../../context/ToastContext';
import { useMyInquiries } from '../../../hooks/useMyInquiries';
import { useMarkInquiryRead } from '../../../hooks/useReachMutations';
import { displayName, type CommerceSession, type InquiryOut } from '../../../api/commerce';
import './InquiriesInbox.css';

interface InquiriesInboxProps {
  session: CommerceSession | null;
}

/** Unread count from a loaded inquiry page — used both here and by the console header badge. */
export function unreadCount(items: InquiryOut[] | undefined): number {
  return items?.filter((i) => !i.is_read).length ?? 0;
}

const InquiriesInbox: React.FC<InquiriesInboxProps> = ({ session }) => {
  const { toast } = useToast();
  const { data, isLoading, isError, error } = useMyInquiries(session);
  const markRead = useMarkInquiryRead(session);

  const onMarkRead = (id: string) => {
    if (markRead.isPending) return;
    markRead.mutate(id, { onError: (err) => toast.error(err.message || 'Could not mark as read.') });
  };

  if (isLoading) return <div className="inbox__state" role="status">Loading your inbox…</div>;
  if (isError) {
    return <div className="inbox__state inbox__state--error" role="alert">Couldn’t load your inbox. {error?.message ?? ''}</div>;
  }

  const items = data?.items ?? [];
  if (items.length === 0) {
    return <div className="inbox__empty" role="status">No inquiries yet. When a buyer asks about a listing, it lands here.</div>;
  }

  return (
    <ul className="inbox">
      {items.map((inq) => (
        <li key={inq.id} className={`inbox__row${inq.is_read ? '' : ' inbox__row--unread'}`} data-testid="inbox-row">
          <div className="inbox__meta">
            <span className="inbox__title" title={inq.listing_title}>{inq.listing_title}</span>
            <span className="inbox__msg">{inq.message}</span>
            <span className="inbox__time">
              {displayName(inq.from_user_name)} · {new Date(inq.created_at).toLocaleString()}
            </span>
          </div>
          {!inq.is_read && (
            <button type="button" className="seller-btn seller-btn--ghost inbox__read"
                    disabled={markRead.isPending} onClick={() => onMarkRead(inq.id)} data-testid="inbox-mark-read">
              Mark read
            </button>
          )}
        </li>
      ))}
    </ul>
  );
};

export default InquiriesInbox;
