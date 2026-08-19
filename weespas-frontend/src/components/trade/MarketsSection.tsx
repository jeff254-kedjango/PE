/* MarketsSection — the WeeStock Markets tile grid on /trade (WeesStock F4, inline variant).
 *
 * The investor glance-surface inline on the trade page: the SAME verified data as the full
 * /markets board (opt-in sellers only, 90-day revenue as the value, momentum as the change,
 * sparkline as the shape, score as the sort key) compressed into a 2×3 grid of tiles. The
 * header carries an arrow "Markets" button that opens the whole board.
 *
 * Boundaries carried over from the board (enforced by the render, not just the API):
 *   1. Only opt-in sellers appear — the API filters; this section never fabricates rows.
 *   2. Discovery/analytics only. The full board carries the regulatory chip; this section is a
 *      glance surface, so it links there instead of restating the label at tile size.
 *   3. The money is the same net-to-seller cents the seller's own card shows.
 *
 * States mirror the market list: shimmer skeleton while loading, honest empty state ("no shops
 * listed yet" — sellers opt in on their card), and an error line. Only the first
 * GRID_COLS × GRID_ROWS entries are shown — the grid is a glance, and the "Markets" button is
 * the path to the whole board.
 */
import React from 'react';
import { Link } from 'react-router-dom';
import { useMarkets } from '../../hooks/useMarkets';
import type { CommerceSession, MarketEntryOut } from '../../api/commerce';
import MarketSparkline from './market/MarketSparkline';
import MarketChangeChip from './market/MarketChangeChip';
import { marketMoney, marketScore } from './market/marketFormat';
import { categoryLabel } from '../../utils/categories';
import Icon from '../ui/Icon';
import './MarketsSection.css';

/** The grid is a fixed 2×3 glance — bounded and tiny, never a scroll. The full board lives at
 *  /markets (reachable from the header button). */
const GRID_COLS = 2;
const GRID_ROWS = 3;
const TILE_COUNT = GRID_COLS * GRID_ROWS;

interface MarketsSectionProps {
  session: CommerceSession | null;
}

const MarketsSection: React.FC<MarketsSectionProps> = ({ session }) => {
  const { data, isLoading, isError, error } = useMarkets(session);
  const tiles = (data?.entries ?? []).slice(0, TILE_COUNT);

  return (
    <section className="markets-section" aria-label="WeesStock Markets" data-testid="markets-section">
      <header className="markets-section__head">
        <h3 className="markets-section__title">WeesStock Markets</h3>
        {/* The whole board — arrow icon then label, per the design. */}
        <Link to="/markets" className="markets-section__link" data-testid="markets-section-link">
          <Icon name="chevronRight" size={14} />
          Markets
        </Link>
      </header>

      {isError ? (
        <p className="markets-section__state markets-section__state--error" role="alert">
          Couldn’t load the market. {error?.message ?? ''}
        </p>
      ) : isLoading && !data ? (
        // Shimmering skeleton tiles matching the real 2×3 footprint so the rail keeps its
        // height before data lands (same pattern as QuickBuys). aria-hidden: purely visual.
        <div className="markets-section__grid" aria-hidden="true" data-testid="markets-section-skeleton">
          {Array.from({ length: TILE_COUNT }).map((_, i) => (
            <div key={i} className="skeleton markets-section__tile-skeleton" />
          ))}
        </div>
      ) : tiles.length === 0 ? (
        <p className="markets-section__empty" data-testid="markets-section-empty">
          No shops are listed yet — sellers appear here after they opt in on their WeesStock card.
        </p>
      ) : (
        <ul className="markets-section__grid" data-testid="markets-section-grid">
          {tiles.map((e) => (
            <li key={e.seller_id} className="markets-section__item">
              <MarketTile entry={e} />
            </li>
          ))}
        </ul>
      )}
    </section>
  );
};

/** One tile: identity on top, sparkline across the middle, momentum + value along the bottom —
 *  the same four zones as a /markets row, stacked for the tile's narrower footprint. */
const MarketTile: React.FC<{ entry: MarketEntryOut }> = ({ entry }) => {
  const category = categoryLabel(entry.category) || 'Uncategorised';
  const score = entry.is_scoreable && entry.score !== null ? marketScore(entry.score) : null;
  return (
    <Link
      to={`/markets/${encodeURIComponent(entry.seller_id)}`}
      className="markets-section__tile"
      data-testid="markets-tile"
      aria-label={`${entry.shop_name}, ${category}${score !== null ? `, Score ${score}` : ''}, ${marketMoney(entry.revenue_cents, entry.currency)}`}
    >
      <span className="markets-section__tile-id">
        <span className="markets-section__tile-name">{entry.shop_name}</span>
        <span className="markets-section__tile-ticker">
          {category}
          {score !== null && (
            <span className="markets-section__tile-score" data-testid="markets-tile-score">{score}</span>
          )}
        </span>
      </span>

      <MarketSparkline
        series={entry.series.series_cents}
        label={`${entry.shop_name}: weekly verified revenue over ${entry.series.window_days} days`}
      />

      <span className="markets-section__tile-foot">
        <MarketChangeChip trend={entry.revenue_trend} />
        <span className="markets-section__tile-value">{marketMoney(entry.revenue_cents, entry.currency)}</span>
      </span>
    </Link>
  );
};

export default MarketsSection;
