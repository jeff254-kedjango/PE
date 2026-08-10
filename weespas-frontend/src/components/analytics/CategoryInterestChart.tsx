import React from 'react';
import {
  ResponsiveContainer, BarChart, Bar, XAxis, YAxis, Tooltip, CartesianGrid, Cell,
} from 'recharts';
import { useCategoryStats } from '../../hooks/useAnalytics';
import type { SinceWindow } from '../../types/analytics';
import './analytics.css';

interface Props {
  token: string | null;
  since: SinceWindow;
}

const BAR_COLORS = ['#84cc16', '#65a30d', '#16a34a', '#059669', '#0d9488', '#0891b2'];

const CategoryInterestChart: React.FC<Props> = ({ token, since }) => {
  const { data, isLoading, error } = useCategoryStats(token, since);

  if (isLoading) return <div className="chart-card chart-card--loading">Loading categories…</div>;
  if (error) return <div className="chart-card chart-card--error">Failed to load categories.</div>;

  const rows = (data ?? []).slice(0, 10);
  if (rows.length === 0) {
    return <div className="chart-card chart-card--empty">No category engagement yet.</div>;
  }

  return (
    <div className="chart-card">
      <h3 className="chart-card__title">Category interest</h3>
      <p className="chart-card__sub">Weighted score: views + 2×searches + 3×favs + 5×inquiries</p>
      <ResponsiveContainer width="100%" height={320}>
        <BarChart data={rows} layout="vertical" margin={{ top: 8, right: 16, bottom: 8, left: 8 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#e5e7eb" />
          <XAxis type="number" stroke="#475569" fontSize={12} />
          <YAxis dataKey="name" type="category" stroke="#0f172a" fontSize={12} width={110} />
          <Tooltip
            cursor={{ fill: 'rgba(132,204,22,0.08)' }}
            contentStyle={{ background: '#0f172a', border: 'none', color: '#fff', borderRadius: 8 }}
            formatter={(value, _name, item) => {
              const r = (item?.payload ?? {}) as {
                view_count?: number;
                search_count?: number;
                favorite_count?: number;
                inquiry_count?: number;
              };
              return [
                `${value}  (v:${r.view_count ?? 0} s:${r.search_count ?? 0} f:${r.favorite_count ?? 0} i:${r.inquiry_count ?? 0})`,
                'Score',
              ];
            }}
          />
          <Bar dataKey="score" radius={[0, 6, 6, 0]}>
            {rows.map((_, i) => (
              <Cell key={i} fill={BAR_COLORS[i % BAR_COLORS.length]} />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
};

export default CategoryInterestChart;
