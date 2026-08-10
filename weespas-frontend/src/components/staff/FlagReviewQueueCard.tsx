import React, { useState } from 'react';
import { useFlagReviewList, useMarkFlagReviewSeen, useRecordFlagReviewView } from '../../hooks/useFlagReviews';
import { useAuth } from '../../context/AuthContext';
import { formatDate } from '../../utils/format';
import type { FlagReview, FlagReviewState } from '../../api/flagReviews';
import './FlagReviewQueueCard.css';

const STATE_LABEL: Record<FlagReviewState, string> = {
  1: 'Cleared',
  2: 'Unsafe',
  3: 'Condemned',
};

/**
 * Flagged-building review queue for the Staff/Admin dashboard — the staff side of the
 * "flag a building" loop. Each row is one recorded structural flag awaiting review:
 *   sent by [flagger] · #id, [aoi] · judgement · note · when · seen by [identity] · views N
 * The first staff/admin to press "Mark seen" is recorded as the acknowledger (first-wins,
 * immutable). Expanding a row records a distinct view. The flagger↔building↔note join is
 * sensitive (work_flow.md §4.2/§9.7), which is why the backend route is staff-gated and
 * this surface is only mounted on the role-gated StaffPage.
 */
const FlagReviewQueueCard: React.FC = () => {
  const { user } = useAuth();
  const [showAll, setShowAll] = useState(false);
  const { data, isLoading, isError } = useFlagReviewList(true, showAll ? 'all' : 'open');
  const markSeen = useMarkFlagReviewSeen();
  const recordView = useRecordFlagReviewView();
  const [expanded, setExpanded] = useState<string | null>(null);

  const toggle = (r: FlagReview) => {
    const next = expanded === r.id ? null : r.id;
    setExpanded(next);
    // Record a distinct view the first time this user opens this row.
    if (next === r.id) recordView.mutate(r.id);
  };

  if (isError) {
    return (
      <section className="chart-card chart-card--error">
        <h3 className="chart-card__title">Flagged buildings</h3>
        <p>Couldn't load the review queue right now.</p>
      </section>
    );
  }

  const reviews = data ?? [];

  return (
    <section className="chart-card flagq">
      <div className="flagq__head">
        <h3 className="chart-card__title">Flagged buildings</h3>
        <label className="flagq__toggle">
          <input
            type="checkbox"
            checked={showAll}
            onChange={(e) => setShowAll(e.target.checked)}
          />
          Show reviewed
        </label>
      </div>
      <p className="chart-card__sub">
        Structural judgements recorded by engineers/authorities. The first to mark one
        seen is recorded as the reviewer.
      </p>

      {isLoading ? (
        <div className="flagq__empty">Loading…</div>
      ) : reviews.length === 0 ? (
        <div className="flagq__empty">
          {showAll ? 'No flags recorded yet.' : 'Nothing awaiting review ✨'}
        </div>
      ) : (
        <ul className="flagq__list">
          {reviews.map((r) => (
            <li
              key={r.id}
              className={`flagq__item${r.seen ? '' : ' flagq__item--open'} flagq__item--state${r.state}`}
            >
              <button type="button" className="flagq__row" onClick={() => toggle(r)} aria-expanded={expanded === r.id}>
                <span className="flagq__main">
                  <span className={`flagq__badge flagq__badge--state${r.state}`}>
                    {STATE_LABEL[r.state]}
                  </span>
                  <span className="flagq__bldg">#{r.insar_building_id}</span>
                  <span className="flagq__aoi">{r.aoi_code}</span>
                </span>
                <span className="flagq__meta">
                  <span className="flagq__by">by {r.flagged_by_name ?? 'unknown'}</span>
                  <span className="flagq__time">{formatDate(r.flagged_at ?? undefined)}</span>
                </span>
              </button>

              {expanded === r.id && (
                <div className="flagq__detail">
                  {r.note ? (
                    <p className="flagq__note">“{r.note}”</p>
                  ) : (
                    <p className="flagq__note flagq__note--empty">No note left.</p>
                  )}
                  <dl className="flagq__facts">
                    <div><dt>Sent by</dt><dd>{r.flagged_by_name ?? 'unknown'}</dd></div>
                    <div><dt>Source</dt><dd>{r.source}</dd></div>
                    <div>
                      <dt>Seen by</dt>
                      <dd>{r.seen ? (r.seen_by_name ?? 'staff') : '—'}</dd>
                    </div>
                    <div><dt>Views</dt><dd>{r.views}</dd></div>
                  </dl>
                  <div className="flagq__actions">
                    {r.seen ? (
                      <span className="flagq__seen-tag">
                        ✓ Reviewed by {r.seen_by_name ?? 'staff'}
                        {r.seen_by_id === user?.id ? ' (you)' : ''}
                      </span>
                    ) : (
                      <button
                        type="button"
                        className="btn btn-primary flagq__seen-btn"
                        onClick={() => markSeen.mutate(r.id)}
                        disabled={markSeen.isPending}
                      >
                        {markSeen.isPending ? 'Marking…' : 'Mark seen'}
                      </button>
                    )}
                  </div>
                </div>
              )}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
};

export default FlagReviewQueueCard;
