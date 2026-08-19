// MarketDetailPage — one shop's WeesStock deep-dive (/markets/:sellerId), §WeesStock F4.
//
// The quote page of the investor surface: the score reads as the headline "price", the
// momentum chip sits under it, the weekly revenue chart is the market's tape, and the
// component bars are the breakdown. The profile is THE SAME shape the seller sees on their
// own card — an investor and a seller can never be told different numbers.
//
// The three credit doctrines carry over to the investor view:
//   1. Components are the product — the bars always render, even when the composite is
//      withheld on a thin file.
//   2. A thin file is NOT a low score — the page shows a growth prompt, never a 0.
//   3. Absolute, never peer-relative — the quote is the shop's own verified history.
//
// Consent is the boundary: an unlisted shop's id 404s exactly like a garbage id (uniform on
// the backend), so the failure copy here is deliberately neutral — "isn't on the market",
// never "doesn't exist".
import React from 'react';
import { Link, useParams } from 'react-router-dom';
import { useAuth } from '../context/AuthContext';
import { useCommerceSession } from '../hooks/useCommerceSession';
import { useMarketDetail } from '../hooks/useMarketDetail';
import type { CreditComponent } from '../api/commerce';
import MarketSparkline from '../components/trade/market/MarketSparkline';
import { marketMoney, marketPct, marketScore, marketTenure, trendDelta, growthPrompt, SCORE_SCALE } from '../components/trade/market/marketFormat';
import { categoryLabel } from '../utils/categories';
import PageMeta from '../components/ui/PageMeta';
import './MarketDetailPage.css';

const MarketDetailPage: React.FC = () => {
  const { isAuthenticated } = useAuth();
  const { sellerId } = useParams<{ sellerId: string }>();
  const { session, isLoading: sessionLoading, error: sessionError } = useCommerceSession();
  const { data, isLoading, isError } = useMarketDetail(session, sellerId);

  if (!isAuthenticated) {
    return (
      <div className="market-detail market-detail--gate">
        <PageMeta title="WeesStock Markets" description="Verified trading history of consenting shops on Weespas." />
        <h1>WeesStock Markets</h1>
        <p>Sign in to view a shop’s trading history.</p>
        <Link to={`/login?next=markets/${encodeURIComponent(sellerId ?? '')}`} className="market-detail__cta">Sign in</Link>
      </div>
    );
  }

  return (
    <div className="market-detail">
      <PageMeta title="WeesStock Markets" description="Verified trading history of consenting shops on Weespas." />
      <Link to="/markets" className="market-detail__back" data-testid="market-detail-back">← Markets</Link>

      {sessionLoading && <p className="market-detail__state">Connecting…</p>}
      {sessionError && (
        <p className="market-detail__state market-detail__state--error" role="alert">
          Couldn’t start a market session. {sessionError.message}
        </p>
      )}
      {isError && (
        <div className="market-detail__state market-detail__state--error" role="alert" data-testid="market-detail-missing">
          This shop isn’t on WeesStock Markets.
        </div>
      )}
      {isLoading && !data && <p className="market-detail__state" role="status">Reading the market…</p>}

      {data && (
        <>
          {/* Quote header — the score as the headline price, in the finance idiom. */}
          <header className="market-detail__quote">
            <div className="market-detail__quote-left">
              <p className="eyebrow">
                WeesStock Markets · {categoryLabel(data.seller.category) || 'Uncategorised'}
              </p>
              <h1 className="market-detail__name" data-testid="market-detail-name">{data.seller.shop_name}</h1>
              <p className="market-detail__owner">Owned by {data.seller.seller_name}</p>
            </div>

            {data.profile.is_scoreable && data.profile.score !== null ? (
              <div className="market-detail__price" data-testid="market-detail-score">
                <span className="market-detail__score">
                  {marketScore(data.profile.score)}
                  <span className="market-detail__score-max">/{SCORE_SCALE}</span>
                </span>
                {data.profile.revenue_trend !== null && (
                  <span className="market-detail__momentum">
                    <span className="market-detail__momentum-label">30d momentum</span>
                    <TrendMark trend={data.profile.revenue_trend} />
                  </span>
                )}
              </div>
            ) : (
              <div className="market-detail__pending" data-testid="market-detail-pending">
                <span className="market-detail__pending-label">Building history</span>
                <span className="market-detail__pending-hint">{growthPrompt(data.profile)}</span>
              </div>
            )}
          </header>

          {/* The tape: weekly verified revenue. */}
          <section className="market-detail__card" aria-labelledby="market-detail-chart-title">
            <div className="market-detail__card-head">
              <h2 id="market-detail-chart-title" className="market-detail__card-title">
                Weekly verified revenue · last {data.series.window_days} days
              </h2>
              <span className="market-detail__card-meta">{data.series.bucket_count} weekly buckets</span>
            </div>
            <MarketSparkline
              series={data.series.series_cents}
              width={880}
              height={180}
              filled
              baseline
              label={`${data.seller.shop_name}: weekly verified revenue over ${data.series.window_days} days`}
            />
            <dl className="market-detail__chart-stats">
              <ChartStat label="Total" value={marketMoney(data.profile.revenue_cents, data.profile.currency)} />
              <ChartStat
                label="Best week"
                value={marketMoney(Math.max(...data.series.series_cents), data.series.currency)}
              />
              <ChartStat
                label="Average / week"
                value={marketMoney(
                  Math.round(data.profile.revenue_cents / data.series.bucket_count),
                  data.series.currency,
                )}
              />
            </dl>
          </section>

          {/* The breakdown — always shown (doctrine 1), same bars as the seller's card. */}
          <section className="market-detail__card" aria-labelledby="market-detail-breakdown-title">
            <div className="market-detail__card-head">
              <h2 id="market-detail-breakdown-title" className="market-detail__card-title">Score breakdown</h2>
              <span className="market-detail__card-meta">What the composite is built from</span>
            </div>
            <ul className="market-detail__components" data-testid="market-detail-components">
              {data.profile.components.map((c) => (
                <ComponentBar key={c.key} c={c} />
              ))}
            </ul>
          </section>

          <dl className="market-detail__facts">
            <Fact label={`Verified sales · ${data.profile.window_days} days`} value={marketMoney(data.profile.revenue_cents, data.profile.currency)} />
            <Fact
              label="Completed orders"
              value={`${data.profile.settled_orders}${data.profile.failed_orders > 0 ? ` · ${marketPct(data.profile.fulfilment_rate)} completed` : ''}`}
            />
            <Fact label="Repeat buyers" value={`${data.profile.repeat_buyers} of ${data.profile.unique_buyers}`} />
            <Fact label="Buyer rating" value={data.profile.rating_count === 0 ? 'Unrated' : `★ ${data.profile.rating.toFixed(1)} (${data.profile.rating_count})`} />
            <Fact label="Average order" value={marketMoney(data.profile.avg_order_value_cents, data.profile.currency)} />
            <Fact label="Trading for" value={marketTenure(data.profile.tenure_days)} />
          </dl>

          <div className="market-detail__foot">
            <p className="market-detail__note" data-testid="market-detail-regulatory">
              Discovery &amp; analytics only — not a securities market. Nothing on this page is an
              offer, solicitation, or financial advice. Investment activity, when it arrives, lives
              behind a separate regulated surface (Kenya: Capital Markets (Investment-Based
              Crowdfunding) Regulations 2022).
            </p>
            <Link to={`/shop/${encodeURIComponent(data.seller.seller_id)}`} className="market-detail__visit">
              Visit shop
            </Link>
          </div>
        </>
      )}
    </div>
  );
};

const TrendMark: React.FC<{ trend: number | null }> = ({ trend }) => {
  if (trend === null) return null;
  const { delta, flat, up } = trendDelta(trend);
  if (flat) {
    return <span className="market-detail__trend" data-testid="market-detail-trend">→</span>;
  }
  return (
    <span
      className={`market-detail__trend ${up ? 'market-detail__trend--up' : 'market-detail__trend--down'}`}
      data-testid="market-detail-trend"
    >
      {up ? '↑' : '↓'} {marketPct(Math.abs(delta))}
    </span>
  );
};

const ChartStat: React.FC<{ label: string; value: string }> = ({ label, value }) => (
  <div className="market-detail__chart-stat">
    <dt>{label}</dt>
    <dd>{value}</dd>
  </div>
);

/** One component bar — identical normalisation to the seller's card: each fills by how much
 *  of ITS OWN weight is earned, so a 0.08-max tenure bar is visually comparable to a 0.40-max
 *  revenue bar. The divide is guarded — a future zero-weight component renders empty, not NaN. */
const ComponentBar: React.FC<{ c: CreditComponent }> = ({ c }) => {
  const filled = c.weight > 0 ? Math.min(1, Math.max(0, c.weighted / c.weight)) : 0;
  return (
    <li className="market-detail__component" data-testid="market-detail-component">
      <div className="market-detail__component-head">
        <span className="market-detail__component-label">{c.label}</span>
        <span className="market-detail__component-weight">{marketPct(c.weight)}</span>
      </div>
      <div
        className="market-detail__bar"
        role="meter"
        aria-valuenow={Math.round(filled * 100)}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-label={`${c.label}: ${marketPct(filled)} of its maximum`}
      >
        <div className="market-detail__bar-fill" style={{ width: `${filled * 100}%` }} />
      </div>
    </li>
  );
};

const Fact: React.FC<{ label: string; value: string }> = ({ label, value }) => (
  <div className="market-detail__fact">
    <dt>{label}</dt>
    <dd>{value}</dd>
  </div>
);

export default MarketDetailPage;
