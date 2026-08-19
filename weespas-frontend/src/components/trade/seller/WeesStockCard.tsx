// WeesStockCard — the seller's own funding readiness, on /trade/sell (§WeesStock F2).
//
// WeesStock is the FINANCING surface: "how fundable is this shop, and why". It is not
// inventory — the units-in-stock card is LowStockCard, a different thing that unfortunately
// shares the English word "stock".
//
// Three doctrines carried from services/credit_score.py, all visible in what this renders:
//
//   1. Components are the product; the composite is a sort key. The breakdown is always shown,
//      at full prominence, even when the composite is withheld. A seller who is told only a
//      number learns nothing about what to change; a lender who is shown only a number cannot
//      underwrite. So the bars are not a drill-down — they are the card.
//
//   2. A thin file is NOT a low score. Below the cold-start gates the server sends
//      `score: null`, and this renders a growth prompt naming exactly what is still missing
//      ("4 more settled sales"). Rendering 0 instead would tell a healthy three-week-old shop
//      it is uncreditworthy, which is both false and discouraging.
//
//   3. Absolute, never peer-relative. Nothing here compares the seller to anyone. That is
//      RankingCard's job, and rank deliberately does not feed credit — a shop must not become
//      more fundable because its neighbours got worse.
//
// Inquiries are shown but explicitly labelled as not counting: a seller can generate inquiries
// at will, so nothing self-generatable may move the score. Saying so on the card is what stops
// it reading as an omission.
import React from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useCreditProfile } from '../../../hooks/useCreditProfile';
import {
  getMarketListing,
  setMarketListing,
  type CommerceSession,
  type CreditProfileOut,
} from '../../../api/commerce';
import {
  marketMoney,
  marketPct,
  marketTenure,
  growthPrompt,
  trendDelta,
} from '../market/marketFormat';
import './WeesStockCard.css';

interface WeesStockCardProps {
  session: CommerceSession | null;
}

/** The composite is a weighted sum of terms in [0, 1], so it is itself in [0, 1]. Sellers read
 *  a 0–100 figure far more naturally than "0.62", and /100 avoids any suggestion that this is
 *  a bank-style 300–850 bureau score, which it is not. */
const SCORE_SCALE = 100;

/** The seller's own WeesStock market consent — server is the source of truth for the switch's
 *  first paint, never a client guess. 5 min cadence: a flag flip is a rare, slow event. */
const LISTING_KEY = ['commerce', 'seller', 'weesstock-listing'] as const;

const WeesStockCard: React.FC<WeesStockCardProps> = ({ session }) => {
  const { data, isLoading, isError, error } = useCreditProfile(session);
  const qc = useQueryClient();

  // §F4 opt-in: appearing on WeesStock Markets is the seller's OWN choice, default off. The
  // switch reads the persisted flag from the server (never a stale guess) and flips it with
  // the owner-only endpoint (there is no id parameter — it can only affect this seller).
  const listingQuery = useQuery({
    queryKey: [...LISTING_KEY, session?.commerce_url],
    queryFn: () => getMarketListing(session!),
    enabled: !!session,
    staleTime: 300_000,
    retry: 1,
  });
  const mutation = useMutation({
    mutationFn: (listed: boolean) => setMarketListing(session!, listed),
    onSuccess: (res) => qc.setQueryData([...LISTING_KEY, session?.commerce_url], res),
  });

  return (
    <section className="weesstock-card" aria-labelledby="weesstock-card-title">
      <header className="weesstock-card__head">
        <h2 id="weesstock-card-title" className="weesstock-card__title">WeesStock</h2>
        <div className="weesstock-card__head-right">
          <span className="weesstock-card__hint" title="Your funding readiness, from verified sales">
            Funding readiness
          </span>
          <label
            className="weesstock-card__listed"
            title={listingQuery.data?.listed
              ? 'Your shop is visible to investors on WeesStock Markets'
              : 'Show your shop to investors on WeesStock Markets (opt-in)'}
          >
            <input
              type="checkbox"
              role="switch"
              data-testid="weesstock-listed"
              checked={listingQuery.data?.listed ?? false}
              disabled={listingQuery.isLoading || listingQuery.isError || mutation.isPending}
              onChange={(e) => mutation.mutate(e.target.checked)}
              aria-label="Listed on WeesStock Markets"
            />
            <span className="weesstock-card__listed-label">Listed on Markets</span>
          </label>
        </div>
      </header>
      {mutation.isError && (
        <p className="weesstock-card__listed-error" role="alert" data-testid="weesstock-listed-error">
          Couldn’t update your market listing.
        </p>
      )}

      <div className="weesstock-card__body">
        {isLoading && (
          <p className="weesstock-card__state" role="status">Reading your trading record…</p>
        )}
        {isError && (
          <p className="weesstock-card__state weesstock-card__state--error" role="alert">
            Couldn’t load your funding profile. {error?.message ?? ''}
          </p>
        )}
        {data && <ProfileBody data={data} />}
      </div>
    </section>
  );
};

const ProfileBody: React.FC<{ data: CreditProfileOut }> = ({ data }) => {
  // Doctrine 2: branch on the explicit flag, never on `score > 0`. A genuine 0.0 composite and
  // a withheld one are different states and must not collapse into the same render.
  const scoreShown = data.is_scoreable && data.score !== null;

  return (
    <>
      <div className="weesstock-card__score-row">
        {scoreShown ? (
          <div className="weesstock-card__quote">
            <div
              className="weesstock-card__score"
              aria-label={`Funding score ${Math.round((data.score as number) * SCORE_SCALE)} out of ${SCORE_SCALE}`}
            >
              <span className="weesstock-card__score-value" data-testid="weesstock-score">
                {Math.round((data.score as number) * SCORE_SCALE)}
              </span>
              <span className="weesstock-card__score-max">/{SCORE_SCALE}</span>
            </div>
            {/* Finance-quote idiom: momentum sits under the headline number, labelled so it
                reads as REVENUE movement — never as the score itself changing. */}
            {data.revenue_trend !== null && (
              <div className="weesstock-card__momentum">
                <span className="weesstock-card__momentum-label">30d revenue momentum</span>
                <TrendMark trend={data.revenue_trend} />
              </div>
            )}
            
          </div>
        ) : (
          <div className="weesstock-card__pending" role="status" data-testid="weesstock-pending">
            <span className="weesstock-card__pending-label">Building history</span>
            <p className="weesstock-card__pending-hint">{growthPrompt(data)}</p>
          </div>
        )}
      </div>

      {/* Always rendered, scoreable or not — doctrine 1. On a thin file these are the ONLY
          honest signal available, and they are exactly what tells a new seller where to push. */}
      <span className="weesstock-card__section">Score breakdown</span>

      <ul className="weesstock-card__components" data-testid="weesstock-components">
        {data.components.map((c) => {
          // `weighted` is post-weight, so its ceiling is the weight itself. Normalising by the
          // weight is what makes a 0.08-max tenure bar visually comparable to a 0.40-max
          // revenue bar: each fills by how much of ITS OWN potential is earned. Guard the
          // divide — a future zero-weight component must render empty, not NaN.
          const filled = c.weight > 0 ? Math.min(1, Math.max(0, c.weighted / c.weight)) : 0;
          return (
            <li key={c.key} className="weesstock-card__component" data-testid="weesstock-component">
              <div className="weesstock-card__component-head">
                <span className="weesstock-card__component-label">{c.label}</span>
                <span className="weesstock-card__component-weight">{marketPct(c.weight)}</span>
              </div>
              <div
                className="weesstock-card__bar"
                role="meter"
                aria-valuenow={Math.round(filled * 100)}
                aria-valuemin={0}
                aria-valuemax={100}
                aria-label={`${c.label}: ${marketPct(filled)} of its maximum`}
              >
                <div className="weesstock-card__bar-fill" style={{ width: `${filled * 100}%` }} />
              </div>
            </li>
          );
        })}
      </ul>

      <dl className="weesstock-card__facts">
        <div className="weesstock-card__fact">
          <dt>Verified sales · {data.window_days} days</dt>
          <dd>{marketMoney(data.revenue_cents, data.currency)}</dd>
        </div>
        <div className="weesstock-card__fact">
          <dt>Completed orders</dt>
          <dd>
            {data.settled_orders}
            {data.failed_orders > 0 && (
              <span className="weesstock-card__fact-sub"> · {marketPct(data.fulfilment_rate)} completed</span>
            )}
          </dd>
        </div>
        <div className="weesstock-card__fact">
          <dt>Repeat buyers</dt>
          <dd>
            {data.repeat_buyers}
            {data.unique_buyers > 0 && (
              <span className="weesstock-card__fact-sub"> of {data.unique_buyers}</span>
            )}
          </dd>
        </div>
        <div className="weesstock-card__fact">
          <dt>Buyer rating</dt>
          <dd>{data.rating_count === 0 ? 'Unrated' : `★ ${data.rating.toFixed(1)} (${data.rating_count})`}</dd>
        </div>
        <div className="weesstock-card__fact">
          <dt>Trading for</dt>
          <dd>{marketTenure(data.tenure_days)}</dd>
        </div>
        <div className="weesstock-card__fact">
          {/* Labelled as excluded ON the card. Inquiries are self-generatable, so they must not
              move the score — but silently omitting them would read as a missing feature. */}
          <dt>Inquiries <span className="weesstock-card__fact-note">· not scored</span></dt>
          <dd>{data.inquiries}</dd>
        </div>
      </dl>

      <p className="weesstock-card__footnote">
        Built only from settled sales on Weespas — money that actually reached you.
      </p>
    </>
  );
};

/** Momentum chip for the recent run-rate — arrow + magnitude, in the finance idiom. `null`
 *  means "no revenue to compare", which is a different statement from "flat" and must not
 *  draw an arrow at all. */
const TrendMark: React.FC<{ trend: number | null }> = ({ trend }) => {
  if (trend === null) return null;
  const { delta, flat, up } = trendDelta(trend);
  if (flat) {
    return <span className="weesstock-card__trend" data-testid="weesstock-trend" title="Steady">→</span>;
  }
  return (
    <span
      className={`weesstock-card__trend ${up ? 'weesstock-card__trend--up' : 'weesstock-card__trend--down'}`}
      data-testid="weesstock-trend"
      title={up ? 'Selling faster than your 90-day average' : 'Selling slower than your 90-day average'}
      aria-label={up ? 'Trending up' : 'Trending down'}
    >
      {up ? '↑' : '↓'} {marketPct(Math.abs(delta))}
    </span>
  );
};

export default WeesStockCard;
