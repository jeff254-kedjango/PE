// Sponsored-cap review queue for the Staff/Admin dashboard — the staff side of the §8.3 per-shop
// cap-override loop. Each row is one pending application: a shop asking to fill more sponsored feed
// slots than the global default. Staff either APPROVE (granting an absolute cap, bounded by the
// server ceiling) or REJECT; only an approved positive cap ever affects the feed.
//
// Auth: the cap admin endpoints live on the commerce service and are staff-gated there (the commerce
// token carries the weespas role claim; require_staff checks it, fail-closed). We mint that commerce
// session via useCommerceSession — this card is only mounted on the already role-gated StaffPage, so
// a non-staff user never sees it, and even if the request were forged the backend rejects it (403).
//
// Anti-drift: the approve input is bounded by the max_cap the LIST response carries, never a
// hard-coded ceiling — the same server-authoritative rule as the seller side.
import React, { useState } from 'react';
import { useCommerceSession } from '../../hooks/useCommerceSession';
import { usePendingSponsoredCaps, useDecideSponsoredCap } from '../../hooks/useSponsoredCap';
import type { CapOverrideOut } from '../../api/commerce';
import './SponsoredCapQueueCard.css';

const SponsoredCapQueueCard: React.FC = () => {
  const { session } = useCommerceSession();
  const { data, isLoading, isError } = usePendingSponsoredCaps(session);
  const decide = useDecideSponsoredCap(session);
  // Per-row approve amount, seeded from each row's requested cap on first edit (below).
  const [amounts, setAmounts] = useState<Record<string, string>>({});
  const [actingId, setActingId] = useState<string | null>(null);

  const maxCap = data?.max_cap ?? null;
  const overrides = data?.overrides ?? [];

  const amountFor = (o: CapOverrideOut): string =>
    amounts[o.id] ?? String(o.requested_cap);

  const approve = (o: CapOverrideOut) => {
    const parsed = Number.parseInt(amountFor(o), 10);
    if (!session || decide.isPending) return;
    if (!Number.isFinite(parsed) || parsed < 1 || (maxCap != null && parsed > maxCap)) return;
    setActingId(o.id);
    decide.mutate(
      { overrideId: o.id, approve: true, approvedCap: parsed },
      { onSettled: () => setActingId(null) },
    );
  };

  const reject = (o: CapOverrideOut) => {
    if (!session || decide.isPending) return;
    setActingId(o.id);
    decide.mutate({ overrideId: o.id, approve: false }, { onSettled: () => setActingId(null) });
  };

  if (isError) {
    return (
      <section className="chart-card chart-card--error">
        <h3 className="chart-card__title">Sponsored-cap requests</h3>
        <p>Couldn’t load the cap review queue right now.</p>
      </section>
    );
  }

  return (
    <section className="chart-card spcapq">
      <h3 className="chart-card__title">Sponsored-cap requests</h3>
      <p className="chart-card__sub">
        Shops asking to fill more <em>sponsored</em> slots than the standard cap. Approving grants an
        absolute cap (max {maxCap ?? '—'}); it buys reach, never organic rank.
      </p>

      {isLoading ? (
        <div className="spcapq__empty">Loading…</div>
      ) : overrides.length === 0 ? (
        <div className="spcapq__empty">Nothing awaiting review ✨</div>
      ) : (
        <ul className="spcapq__list">
          {overrides.map((o) => {
            const busy = decide.isPending && actingId === o.id;
            const amount = amountFor(o);
            const parsed = Number.parseInt(amount, 10);
            const valid = Number.isFinite(parsed) && parsed >= 1 && (maxCap == null || parsed <= maxCap);
            return (
              <li key={o.id} className="spcapq__item" data-testid="spcapq-item">
                <div className="spcapq__info">
                  <span className="spcapq__shop">Shop {o.shop_id.slice(0, 8)}</span>
                  <span className="spcapq__req">requested <strong>{o.requested_cap}</strong> slots</span>
                </div>
                <div className="spcapq__actions">
                  <label className="spcapq__amount">
                    <span className="sr-only">Approved cap (max {maxCap})</span>
                    <input
                      type="number"
                      min={1}
                      max={maxCap ?? undefined}
                      step={1}
                      inputMode="numeric"
                      value={amount}
                      disabled={busy}
                      onChange={(e) => setAmounts((prev) => ({ ...prev, [o.id]: e.target.value }))}
                      data-testid={`spcapq-amount-${o.id}`}
                    />
                  </label>
                  <button
                    type="button"
                    className="btn btn-primary spcapq__approve"
                    disabled={busy || !valid}
                    onClick={() => approve(o)}
                    data-testid={`spcapq-approve-${o.id}`}
                  >
                    {busy ? '…' : 'Approve'}
                  </button>
                  <button
                    type="button"
                    className="btn spcapq__reject"
                    disabled={busy}
                    onClick={() => reject(o)}
                    data-testid={`spcapq-reject-${o.id}`}
                  >
                    Reject
                  </button>
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
};

export default SponsoredCapQueueCard;
