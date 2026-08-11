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
import { useCreditProfile } from '../../../hooks/useCreditProfile';
import type { CommerceSession, CreditProfileOut } from '../../../api/commerce';
import './WeesStockCard.css';

interface WeesStockCardProps {
  session: CommerceSession | null;
}

/** The composite is a weighted sum of terms in [0, 1], so it is itself in [0, 1]. Sellers read
 *  a 0–100 figure far more naturally than "0.62", and /100 avoids any suggestion that this is
 *  a bank-style 300–850 bureau score, which it is not. */
const SCORE_SCALE = 100;

/** Trend deltas below this read as noise, not movement — a 3% swing over a 30-day window is
 *  ordinary week-to-week variation and must not be drawn as an arrow. */
const TREND_FLAT_BAND = 0.05;

/** Cents → "KSh 12,300", grouped and without decimals. Kenyan retail prices are quoted in
 *  whole shillings; cents exist in the ledger for exactness, not for display. */
function money(cents: number, currency: string): string {
  const major = Math.round(cents / 100);
  return `${currency === 'KES' ? 'KSh' : currency} ${major.toLocaleString('en-KE')}`;
}

function pct(rate: number): string {
  return `${Math.round(rate * 100)}%`;
}

/** Tenure in the largest honest unit. "428 days" is a number a seller has to convert; the
 *  point of the field is "how long have I been trading", which months and years answer. */
function tenure(days: number): string {
  if (days < 1) return 'New today';
  if (days < 60) return `${Math.round(days)} day${Math.round(days) === 1 ? '' : 's'}`;
  if (days < 730) return `${Math.floor(days / 30)} months`;
  const years = Math.floor(days / 365);
  return `${years} year${years === 1 ? '' : 's'}`;
}

/** Turn the server's machine-readable gate reasons into one sentence a seller can act on.
 *  The counts come from the server (orders_needed / days_needed) so the thresholds themselves
 *  are never duplicated in the client, where they could drift from the service constants. */
function growthPrompt(data: CreditProfileOut): string {
  const parts: string[] = [];
  if (data.orders_needed > 0) {
    parts.push(`${data.orders_needed} more completed sale${data.orders_needed === 1 ? '' : 's'}`);
  }
  if (data.days_needed > 0) {
    parts.push(`${data.days_needed} more day${data.days_needed === 1 ? '' : 's'} of trading`);
  }
  if (parts.length === 0) return 'Building your funding score.';
  return `${parts.join(' and ')} to unlock your funding score.`;
}

const WeesStockCard: React.FC<WeesStockCardProps> = ({ session }) => {
  const { data, isLoading, isError, error } = useCreditProfile(session);

  return (
    <section className="weesstock-card" aria-labelledby="weesstock-card-title">
      <header className="weesstock-card__head">
        <h2 id="weesstock-card-title" className="weesstock-card__title">WeesStock</h2>
        <span className="weesstock-card__hint" title="Your funding readiness, from verified sales">
          Funding readiness
        </span>
      </header>

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
          <div
            className="weesstock-card__score"
            aria-label={`Funding score ${Math.round((data.score as number) * SCORE_SCALE)} out of ${SCORE_SCALE}`}
          >
            <span className="weesstock-card__score-value" data-testid="weesstock-score">
              {Math.round((data.score as number) * SCORE_SCALE)}
            </span>
            <span className="weesstock-card__score-max">/{SCORE_SCALE}</span>
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
                <span className="weesstock-card__component-weight">{pct(c.weight)}</span>
              </div>
              <div
                className="weesstock-card__bar"
                role="meter"
                aria-valuenow={Math.round(filled * 100)}
                aria-valuemin={0}
                aria-valuemax={100}
                aria-label={`${c.label}: ${pct(filled)} of its maximum`}
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
          <dd>
            {money(data.revenue_cents, data.currency)}
            <TrendMark trend={data.revenue_trend} />
          </dd>
        </div>
        <div className="weesstock-card__fact">
          <dt>Completed orders</dt>
          <dd>
            {data.settled_orders}
            {data.failed_orders > 0 && (
              <span className="weesstock-card__fact-sub"> · {pct(data.fulfilment_rate)} completed</span>
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
          <dd>{tenure(data.tenure_days)}</dd>
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

/** Trend arrow for the recent run-rate. `null` means "no revenue to compare", which is a
 *  different statement from "flat" and must not draw an arrow at all. */
const TrendMark: React.FC<{ trend: number | null }> = ({ trend }) => {
  if (trend === null) return null;
  const delta = trend - 1;
  if (Math.abs(delta) < TREND_FLAT_BAND) {
    return <span className="weesstock-card__trend" data-testid="weesstock-trend" title="Steady">→</span>;
  }
  const up = delta > 0;
  return (
    <span
      className={`weesstock-card__trend ${up ? 'weesstock-card__trend--up' : 'weesstock-card__trend--down'}`}
      data-testid="weesstock-trend"
      title={up ? 'Selling faster than your 90-day average' : 'Selling slower than your 90-day average'}
      aria-label={up ? 'Trending up' : 'Trending down'}
    >
      {up ? '↑' : '↓'}
    </span>
  );
};

export default WeesStockCard;
