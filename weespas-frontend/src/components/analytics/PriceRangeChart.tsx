import React, { useEffect, useRef, useState } from 'react';
import {
  ResponsiveContainer, BarChart, Bar, XAxis, YAxis, Tooltip, CartesianGrid, Legend,
} from 'recharts';
import { usePriceStats } from '../../hooks/useAnalytics';
import type { SinceWindow } from '../../types/analytics';
import './analytics.css';

interface Props {
  token: string | null;
  since: SinceWindow;
}

type Mode = 'sale' | 'rent';

const PriceRangeChart: React.FC<Props> = ({ token, since }) => {
  const [mode, setMode] = useState<Mode>('sale');
  const userTouchedRef = useRef(false);
  // Re-enable auto-flip whenever the time window changes — the previous
  // data's emptiness shouldn't lock the user's mode forever.
  useEffect(() => {
    userTouchedRef.current = false;
  }, [since]);

  // Fetch BOTH series so we can decide whether to auto-flip the toggle.
  const saleQ = usePriceStats(token, since, 'sale');
  const rentQ = usePriceStats(token, since, 'rent');
  const isLoading = saleQ.isLoading || rentQ.isLoading;
  const error = saleQ.error || rentQ.error;

  const saleRows = saleQ.data?.sale ?? [];
  const rentRows = rentQ.data?.rent ?? [];
  const saleTotal = saleRows.reduce((s, r) => s + r.score, 0);
  const rentTotal = rentRows.reduce((s, r) => s + r.score, 0);

  // Auto-flip once: if the chosen mode is empty and the other has data, switch.
  useEffect(() => {
    if (userTouchedRef.current) return;
    if (isLoading) return;
    if (mode === 'sale' && saleTotal === 0 && rentTotal > 0) setMode('rent');
    else if (mode === 'rent' && rentTotal === 0 && saleTotal > 0) setMode('sale');
  }, [isLoading, mode, saleTotal, rentTotal]);

  const handleModeChange = (next: Mode) => {
    userTouchedRef.current = true;
    setMode(next);
  };

  if (isLoading) return <div className="chart-card chart-card--loading">Loading prices…</div>;
  if (error) return <div className="chart-card chart-card--error">Failed to load prices.</div>;

  const rows = mode === 'sale' ? saleRows : rentRows;
  const total = mode === 'sale' ? saleTotal : rentTotal;

  return (
    <div className="chart-card">
      <div className="chart-card__head">
        <div>
          <h3 className="chart-card__title">Spending power</h3>
          <p className="chart-card__sub">Engagement weighted by price bucket (KES) — views + 3×favs + 5×inquiries</p>
        </div>
        <div className="chart-card__toggle">
          <button
            type="button"
            className={mode === 'sale' ? 'is-on' : ''}
            onClick={() => handleModeChange('sale')}
          >Sale</button>
          <button
            type="button"
            className={mode === 'rent' ? 'is-on' : ''}
            onClick={() => handleModeChange('rent')}
          >Rent</button>
        </div>
      </div>
      {total === 0 ? (
        <p className="chart-card__empty">No {mode} engagement yet.</p>
      ) : (
        <ResponsiveContainer width="100%" height={320}>
          <BarChart data={rows} margin={{ top: 8, right: 16, bottom: 8, left: 8 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#e5e7eb" />
            <XAxis dataKey="bucket" stroke="#475569" fontSize={11} angle={-15} textAnchor="end" height={60} />
            <YAxis stroke="#475569" fontSize={12} />
            <Tooltip
              contentStyle={{ background: '#0f172a', border: 'none', color: '#fff', borderRadius: 8 }}
            />
            <Legend />
            <Bar
              dataKey="score"
              name={`${mode === 'sale' ? 'Sale' : 'Rent'} engagement`}
              fill={mode === 'sale' ? '#059669' : '#84cc16'}
              radius={[6, 6, 0, 0]}
            />
          </BarChart>
        </ResponsiveContainer>
      )}
    </div>
  );
};

export default PriceRangeChart;
