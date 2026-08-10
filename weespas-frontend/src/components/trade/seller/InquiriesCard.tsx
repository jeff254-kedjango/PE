// InquiriesCard — the LEFT-column wrapper around the seller's Inquiries inbox (§8, Chunk D).
//
// The card is a THIN chrome around the existing InquiriesInbox: title, unread-count badge next to
// the header (mirrors ViewingCard's (N) pattern), then the inbox itself as-is. Keeping the inbox
// component reusable — the card is UI relocation, not a rebuild.
//
// Structure:
//   ┌ Inquiries (2)? ─────────────────────────────┐
//   │   <existing InquiriesInbox — one hook,     │
//   │    same query cache, same mark-read UX>    │
//   └────────────────────────────────────────────┘
//
// The unread count is optional — it renders ONLY when > 0. That matches the "Viewing (3)" pattern:
// when there's nothing to signal, the header stays clean.
import React from 'react';
import InquiriesInbox, { unreadCount } from './InquiriesInbox';
import { useMyInquiries } from '../../../hooks/useMyInquiries';
import type { CommerceSession } from '../../../api/commerce';
import './InquiriesCard.css';

interface InquiriesCardProps {
  session: CommerceSession | null;
}

const InquiriesCard: React.FC<InquiriesCardProps> = ({ session }) => {
  // Read the same cached query the inbox reads (React Query dedupes) — this is JUST for the
  // header badge count. No extra network round-trip.
  const { data } = useMyInquiries(session);
  const unread = unreadCount(data?.items);

  return (
    <section className="inquiries-card" aria-labelledby="inquiries-card-title">
      <header className="inquiries-card__head">
        <h2 id="inquiries-card-title" className="inquiries-card__title">
          Inquiries
          {unread > 0 && (
            <span className="inquiries-card__unread" aria-label={`${unread} unread`}>
              {' '}({unread > 9 ? '9+' : unread})
            </span>
          )}
        </h2>
      </header>
      <div className="inquiries-card__body">
        <InquiriesInbox session={session} />
      </div>
    </section>
  );
};

export default InquiriesCard;
