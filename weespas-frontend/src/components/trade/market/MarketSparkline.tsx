// MarketSparkline — the pure SVG line of a revenue series, shared by the market list rows
// (small, unfilled) and the detail-page chart (tall, filled, dashed baseline).
//
// Deliberately tiny and dependency-free: a series is 13 weekly buckets, so the whole thing is
// a single <polyline> + optional area path. No chart library is worth the bundle for that.
//
// Tone is derived from the SERIES ITSELF (first vs last bucket), not from the caller's
// momentum chip — the sparkline says "what the last 90 days looked like", the chip says
// "30d vs 90d run-rate", and conflating the two would let them disagree with each other.
import React, { useId } from 'react';
import './MarketSparkline.css';

interface MarketSparklineProps {
  /** Weekly net-revenue cents, oldest→newest (13 points from the API). */
  series: number[];
  width?: number;
  height?: number;
  /** Fill the area under the line with a gradient (the detail-page chart). */
  filled?: boolean;
  /** Draw a dashed line at the series mean (the screenshot idiom's zero-line). */
  baseline?: boolean;
  /** Accessible name — the caller supplies the shop/currency context. */
  label: string;
}

const PAD = 2;

/** The tone color for a series direction — up green, down red, flat muted. */
function toneOf(series: number[]): string {
  const first = series[0];
  const last = series[series.length - 1];
  if (last > first) return 'var(--color-success, #15803d)';
  if (last < first) return 'var(--color-danger, #b91c1c)';
  return 'var(--color-text-muted, #6b7280)';
}

const MarketSparkline: React.FC<MarketSparklineProps> = ({
  series,
  width = 96,
  height = 36,
  filled = false,
  baseline = false,
  label,
}) => {
  const uid = useId().replace(/[:]/g, '');
  const n = series.length;
  if (n < 2) return null;

  // Scale into the viewBox with a tiny vertical pad. An all-equal series (incl. all-zero)
  // draws a flat line at the vertical middle — "no movement", not "no data".
  const max = Math.max(...series);
  const min = Math.min(...series);
  const span = max - min;
  const pts = series.map((v, i) => {
    const x = (i / (n - 1)) * width;
    const y = span === 0 ? height / 2 : height - PAD - ((v - min) / span) * (height - PAD * 2);
    return { x, y };
  });
  const line = pts.map((p) => `${p.x.toFixed(2)},${p.y.toFixed(2)}`).join(' ');
  const area =
    filled && span > 0
      ? `${line} ${width.toFixed(2)},${height} 0,${height}`
      : null;
  const mean = series.reduce((a, b) => a + b, 0) / n;
  const meanY = span === 0 ? height / 2 : height - PAD - ((mean - min) / span) * (height - PAD * 2);
  const tone = toneOf(series);

  return (
    <svg
      className="market-sparkline"
      width={width}
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      role="img"
      aria-label={label}
      style={{ color: tone }}
    >
      {area && (
        <defs>
          <linearGradient id={`spark-fill-${uid}`} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="currentColor" stopOpacity="0.28" />
            <stop offset="100%" stopColor="currentColor" stopOpacity="0" />
          </linearGradient>
        </defs>
      )}
      {baseline && span > 0 && (
        <line
          x1={0}
          y1={meanY}
          x2={width}
          y2={meanY}
          className="market-sparkline__baseline"
          stroke="currentColor"
          strokeDasharray="3 3"
        />
      )}
      {area && <polygon points={area} fill={`url(#spark-fill-${uid})`} />}
      <polyline
        className="market-sparkline__line"
        points={line}
        fill="none"
        stroke="currentColor"
        strokeWidth={filled ? 2 : 1.5}
        strokeLinejoin="round"
        strokeLinecap="round"
      />
    </svg>
  );
};

export default MarketSparkline;
