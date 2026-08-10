// RankingCard — the LEFT-column headline of /trade/sell (§8, Chunk B).
//
// Shows the caller's rank in a radius around their shop, refreshed every 5 minutes. The rank
// itself is the visual anchor: a large bold number, everything else framed as supporting detail
// (peer count, weight breakdown tooltip, radius picker). Three response shapes, one card:
//   * kind='ranking'          → the normal card body (rank + signals).
//   * kind='paywall_required' → CTA offering one-time-2h or annual entitlement (deferred; no
//                                payment integration yet — the buttons emit an inert "coming soon"
//                                nudge). We surface this rather than clamp the slider to 200 km
//                                so the seller LEARNS the paywall exists.
//   * kind='no_shop'          → "open a shop to see your ranking" hint (no fetch retry loop).
//
// Radius picker: a range input, default 10 km, bounded 1..1000 (the server caps at 20_000; a 1000
// cap here keeps the slider's dynamic range legible — anyone selling in a 1000 km radius is a
// very unusual outlier and can type into the number field beside it). No debounce on the range
// input — the hook's staleTime keeps refetches at 5 min, and a slider drag past the 200 km line
// intentionally trips the paywall so the user sees where the free tier ends.
import React, { useState } from 'react';
import { useSellerRanking } from '../../../hooks/useSellerRanking';
import type { CommerceSession, RankingResponse } from '../../../api/commerce';
import './RankingCard.css';

interface RankingCardProps {
  session: CommerceSession | null;
}

/** Default radius: the tightest ring worth ranking against — the immediate neighbourhood. */
const DEFAULT_RADIUS_KM = 10;
/** Slider UX cap. The server accepts up to 20_000 km; here we clamp to a range a slider handle
 *  can actually navigate. Above the free cap (200) the payload is a paywall response — the
 *  slider is still allowed to go higher so the user can SEE that. */
const RADIUS_MIN = 1;
const RADIUS_MAX = 1000;
/** Below this the response is the free tier; above triggers the paywall response. Mirrors the
 *  server's _FREE_RADIUS_KM — a small duplication in exchange for a cheap "you're about to hit
 *  the paywall" hint at the slider level. */
const FREE_RADIUS_KM = 200;

function paywallLabel(kind: 'one_time_2h' | 'annual'): string {
  return kind === 'one_time_2h' ? 'One-time · 2 hours' : 'Annual pass';
}

const RankingCard: React.FC<RankingCardProps> = ({ session }) => {
  const [radiusKm, setRadiusKm] = useState<number>(DEFAULT_RADIUS_KM);
  const { data, isLoading, isError, error } = useSellerRanking(session, radiusKm);
  // The paywall CTA is a stub — payment integration lands in a later chunk. Rather than a dead
  // handler we surface a transient "coming soon" nudge, so the button isn't silently no-op.
  const [pendingKind, setPendingKind] = useState<null | 'one_time_2h' | 'annual'>(null);

  const onRadiusChange = (event: React.ChangeEvent<HTMLInputElement>) => {
    const next = Number(event.target.value);
    if (Number.isFinite(next) && next > 0) setRadiusKm(next);
  };

  return (
    <section className="ranking-card" aria-labelledby="ranking-card-title">
      <header className="ranking-card__head">
        <h2 id="ranking-card-title" className="ranking-card__title">Shop ranking</h2>
        <span className="ranking-card__hint" title="Rankings refresh every 5 minutes">Refreshes every 5 min</span>
      </header>

      <div className="ranking-card__body">
        {isLoading && (
          <p className="ranking-card__state" role="status">Reading your peer set…</p>
        )}
        {isError && (
          <p className="ranking-card__state ranking-card__state--error" role="alert">
            Couldn’t load your ranking. {error?.message ?? ''}
          </p>
        )}
        {data && renderBody(data, pendingKind, setPendingKind)}
      </div>

      <div className="ranking-card__radius">
        <label htmlFor="ranking-card-radius" className="ranking-card__radius-label">
          Radius <strong>{radiusKm} km</strong>
          {radiusKm > FREE_RADIUS_KM && (
            <span className="ranking-card__paywall-hint" title="Above the free 200 km tier">· paid</span>
          )}
        </label>
        <input
          id="ranking-card-radius"
          type="range"
          min={RADIUS_MIN}
          max={RADIUS_MAX}
          value={radiusKm}
          step={1}
          onChange={onRadiusChange}
          aria-label="Ranking radius in kilometres"
        />
      </div>
    </section>
  );
};

function renderBody(
  data: RankingResponse,
  pendingKind: null | 'one_time_2h' | 'annual',
  setPendingKind: (k: null | 'one_time_2h' | 'annual') => void,
): React.ReactElement {
  if (data.kind === 'no_shop') {
    return (
      <div className="ranking-card__empty" role="status">
        <p>Open a shop to see where you rank in your neighbourhood.</p>
      </div>
    );
  }
  if (data.kind === 'paywall_required') {
    return (
      <div className="ranking-card__paywall" role="region" aria-label="Paywall">
        <p className="ranking-card__paywall-lead">
          Ranking beyond {data.free_max_radius_km} km is a paid feature.
        </p>
        <p className="ranking-card__paywall-sub">
          You asked for {data.requested_radius_km} km — choose how you’d like access.
        </p>
        <div className="ranking-card__paywall-cta">
          {data.cta_kinds.map((kind) => (
            <button
              key={kind}
              type="button"
              className="ranking-card__cta-btn"
              onClick={() => setPendingKind(kind)}
              aria-pressed={pendingKind === kind}
            >
              {paywallLabel(kind)}
            </button>
          ))}
        </div>
        {pendingKind && (
          <p className="ranking-card__paywall-nudge" role="status">
            Payments arrive soon — we’ve noted your interest in the {paywallLabel(pendingKind)}.
          </p>
        )}
      </div>
    );
  }
  // kind === 'ranking'
  const isUnrated = data.signals.rating_count === 0;
  const revenueMajor = (data.signals.revenue_cents / 100).toFixed(0);
  return (
    <div className="ranking-card__rank">
      <div className="ranking-card__rank-num" aria-label={`Rank ${data.rank} of ${data.peer_count}`}>
        <span className="ranking-card__rank-hash">#</span>
        <span className="ranking-card__rank-value">{data.rank}</span>
      </div>
      <p className="ranking-card__rank-caption">
        of {data.peer_count} shop{data.peer_count === 1 ? '' : 's'} within {data.radius_km} km
      </p>
      <dl className="ranking-card__signals">
        <div className="ranking-card__signal">
          <dt>Sales · last {data.signals.revenue_window_days} days</dt>
          <dd>KSh {revenueMajor}</dd>
        </div>
        <div className="ranking-card__signal">
          <dt>Rating</dt>
          <dd>{isUnrated ? 'Unrated' : `★ ${data.signals.rating.toFixed(1)} (${data.signals.rating_count})`}</dd>
        </div>
        <div className="ranking-card__signal">
          <dt>Followers</dt>
          <dd>{data.signals.follower_count}</dd>
        </div>
        <div className="ranking-card__signal">
          <dt>Saves</dt>
          <dd>{data.signals.saves_total}</dd>
        </div>
      </dl>
    </div>
  );
}

export default RankingCard;
