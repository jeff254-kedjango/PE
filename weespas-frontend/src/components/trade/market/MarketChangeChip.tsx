// MarketChangeChip — the signed momentum readout in the investor market idiom: green up / red
// down / muted flat. Shared by the /markets list rows and the /trade MarketsSection tiles so the
// two surfaces can never phrase momentum differently.
//
// (The seller's OWN card deliberately uses amber for down — this is the investor surface, where
// red/green is the universal convention and the reader expects it. The distinction lives in the
// CSS, not here.)
import React from 'react';
import { marketPct, trendDelta } from './marketFormat';
import './MarketChangeChip.css';

const MarketChangeChip: React.FC<{ trend: number | null }> = ({ trend }) => {
  if (trend === null) return <span className="market-change" data-testid="market-change">—</span>;
  const { delta, flat, up } = trendDelta(trend);
  if (flat) {
    return <span className="market-change market-change--flat" data-testid="market-change">→</span>;
  }
  const cls = up ? 'market-change--up' : 'market-change--down';
  return (
    <span
      className={`market-change ${cls}`}
      data-testid="market-change"
      aria-label={up ? 'Rising' : 'Falling'}
    >
      {up ? '↑' : '↓'} {marketPct(Math.abs(delta))}
    </span>
  );
};

export default MarketChangeChip;
