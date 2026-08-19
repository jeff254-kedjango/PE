// MarketsPage — the WeesStock investor market list (/markets), §WeesStock F4.
//
// The stock-exchange-for-SMEs direction: NSE serves only the top of the market; this is the
// honest data layer under that goal — every CONSENTING seller's verified trading history as
// a ticker row (90-day revenue = the value, momentum = the change, sparkline = the shape,
// score = the sort key).
//
// Two boundaries carry over from the backend and are enforced by the RENDER, not just the
// API:
//   1. Only opt-in sellers appear (the API already filters; the page never fabricates rows).
//   2. This is DISCOVERY/ANALYTICS ONLY. The regulatory chip is not decorative legal copy —
//      it is the line that keeps this surface honest until a separate, regulated investment
//      surface exists (Kenya: Capital Markets (Investment-Based Crowdfunding) Regulations
//      2022). Nothing on this page transacts.
//
// The money shown is net-to-seller cents from settled receipts — the same number the seller's
// own card shows. A market that quoted a different number than the shop's own card would be
// lying in one of two places.
import React from 'react';
import { Link } from 'react-router-dom';
import { useAuth } from '../context/AuthContext';
import { useCommerceSession } from '../hooks/useCommerceSession';
import { useMarkets, MARKETS_POLL_MS } from '../hooks/useMarkets';
import MarketSparkline from '../components/trade/market/MarketSparkline';
import MarketChangeChip from '../components/trade/market/MarketChangeChip';
import { marketMoney, marketScore } from '../components/trade/market/marketFormat';
import { categoryLabel } from '../utils/categories';
import PageMeta from '../components/ui/PageMeta';
import './MarketsPage.css';

/** How many of the strongest sellers lead the "Top" strip. Bounded and tiny — the strip is
 *  a glance, not a ranking. */
const TOP_STRIP_COUNT = 3;

const MarketsPage: React.FC = () => {
  const { isAuthenticated } = useAuth();
  const { session, isLoading: sessionLoading, error: sessionError } = useCommerceSession();
  const { data, isLoading, isError, error } = useMarkets(session);

  if (!isAuthenticated) {
    return (
      <div className="markets-page markets-page--gate">
        <PageMeta title="WeesStock Markets" description="Verified trading history of consenting shops on Weespas." />
        <h1>WeesStock Markets</h1>
        <p>Sign in to browse the market.</p>
        <Link to="/login?next=markets" className="markets-page__cta">Sign in</Link>
      </div>
    );
  }

  return (
    <div className="markets-page">
      <PageMeta title="WeesStock Markets" description="Verified trading history of consenting shops on Weespas." />

      {/* Breadcrumb — the one-step way back to the trade page this board was opened from. The
          current page is marked aria-current and is not a link (it is where you are). */}
      <nav className="markets-page__crumb" aria-label="Breadcrumb" data-testid="markets-breadcrumb">
        <Link to="/trade">Trade</Link>
        <span className="markets-page__crumb-sep" aria-hidden="true">/</span>
        <span className="markets-page__crumb-current" aria-current="page">WeesStock Markets</span>
      </nav>

      <header className="markets-page__header">
        <div>
          <p className="eyebrow">WeesStock</p>
          <h1>Markets</h1>
          <p className="markets-page__sub">
            Verified trading history of shops that chose to be listed · updated every {MARKETS_POLL_MS / 60_000} min
          </p>
        </div>
        <span className="markets-page__regulatory" data-testid="markets-regulatory-chip">
          Discovery &amp; analytics only — not a securities market
        </span>
      </header>

      {sessionLoading && <p className="markets-page__state">Connecting…</p>}
      {sessionError && (
        <p className="markets-page__state markets-page__state--error" role="alert">
          Couldn’t start a market session. {sessionError.message}
        </p>
      )}
      {isError && (
        <p className="markets-page__state markets-page__state--error" role="alert">
          Couldn’t load the market. {error?.message ?? ''}
        </p>
      )}
      {isLoading && !data && (
        <p className="markets-page__state" role="status">Reading the market…</p>
      )}

      {data && (
        <>
          {data.entries.length === 0 ? (
            <div className="markets-page__empty" data-testid="markets-empty">
              <p className="markets-page__empty-title">No shops are listed yet</p>
              <p className="markets-page__empty-sub">
                Sellers appear here only after they opt in on their WeesStock card. The market
                never fabricates rows.
              </p>
            </div>
          ) : (
            <>
              {/* The "Top" strip — the strongest few as glanceable chips, in the idiom of a
                  finance app's popular-tickers row. Same data as the list below; only the
                  layout differs. */}
              <div className="markets-page__top" aria-label="Top shops">
                {data.entries.slice(0, TOP_STRIP_COUNT).map((e) => (
                  <TopChip key={e.seller_id} sellerId={e.seller_id} name={e.shop_name} entry={e} />
                ))}
              </div>

              <ul className="markets-page__list" data-testid="markets-list">
                {data.entries.map((e) => (
                  <li key={e.seller_id}>
                    <Link
                      to={`/markets/${encodeURIComponent(e.seller_id)}`}
                      className="markets-page__row"
                      data-testid="market-row"
                    >
                      <span className="markets-page__row-id">
                        <span className="markets-page__row-name">{e.shop_name}</span>
                        <span className="markets-page__row-ticker">
                          {categoryLabel(e.category) || 'Uncategorised'}
                          {e.is_scoreable && e.score !== null && (
                            <span className="markets-page__row-score" data-testid="market-row-score">
                              Score {marketScore(e.score)}
                            </span>
                          )}
                        </span>
                      </span>

                      <MarketSparkline
                        series={e.series.series_cents}
                        label={`${e.shop_name}: weekly verified revenue over ${e.series.window_days} days`}
                      />

                      <span className="markets-page__row-right">
                        <MarketChangeChip trend={e.revenue_trend} />
                        <span className="markets-page__row-value">{marketMoney(e.revenue_cents, e.currency)}</span>
                      </span>
                    </Link>
                  </li>
                ))}
              </ul>

              <p className="markets-page__foot">
                Value is {data.window_days}-day verified revenue from settled sales — money that
                actually reached the seller. Nothing here is an offer, solicitation, or
                financial advice.
              </p>
            </>
          )}
        </>
      )}
    </div>
  );
};

/** One pill in the top strip: name + momentum, linking to the detail page. */
const TopChip: React.FC<{ sellerId: string; name: string; entry: { revenue_trend: number | null } }> =
  ({ sellerId, name, entry }) => (
    <Link to={`/markets/${encodeURIComponent(sellerId)}`} className="markets-page__chip" data-testid="market-top-chip">
      <span className="markets-page__chip-name">{name}</span>
      <MarketChangeChip trend={entry.revenue_trend} />
    </Link>
  );

export default MarketsPage;
