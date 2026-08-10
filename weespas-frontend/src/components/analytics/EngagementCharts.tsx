// src/components/analytics/EngagementCharts.tsx
//
// Three line charts for the Staff dashboard:
//   • Users  — return-time vs avg session length
//   • Agents — return-time vs avg session length
//   • Staff  — return-time vs avg session length
//
// Performance notes (perf is our competitive edge):
// - ONE network round-trip via `useEngagement` returns all three role series;
//   we slice it locally so we don't fan out 3× identical requests.
// - The presentational chart is memoized so toggling the `since` window
//   only re-renders the parent, and each child rerenders only if its own
//   slice identity changes (react-query returns referentially-stable data
//   when nothing has changed).
// - Recharts' ResponsiveContainer is the expensive bit on first paint —
//   we set an explicit `height` so it doesn't measure the parent on every
//   layout pass.
// - Empty/loading/error states render WITHOUT mounting recharts so a slow
//   network never blocks first paint.

import React, { useMemo, useState } from 'react';
import {
  ResponsiveContainer, LineChart, Line, XAxis, YAxis, Tooltip, CartesianGrid, Legend,
} from 'recharts';
import { useEngagement } from '../../hooks/useAnalytics';
import type {
  EngagementPoint, EngagementRole, EngagementResponse, SinceWindow,
} from '../../types/analytics';
import './analytics.css';
import './EngagementCharts.css';

const SINCE_WINDOWS: { value: SinceWindow; label: string }[] = [
  { value: '7d', label: '7 days' },
  { value: '30d', label: '30 days' },
  { value: '90d', label: '90 days' },
  { value: 'all', label: 'All time' },
];

const ROLE_LABELS: Record<EngagementRole, { title: string; subject: string }> = {
  user:  { title: 'User engagement',  subject: 'Users' },
  agent: { title: 'Agent engagement', subject: 'Agents' },
  staff: { title: 'Staff engagement', subject: 'Staff' },
};

// Distinct hues per role so the three stacked charts read as related-but-distinct.
const ROLE_COLORS: Record<EngagementRole, { ret: string; usg: string }> = {
  user:  { ret: '#0891b2', usg: '#84cc16' },
  agent: { ret: '#7c3aed', usg: '#f59e0b' },
  staff: { ret: '#0f766e', usg: '#dc2626' },
};

// Format the date axis compactly (e.g. "May 14"). The backend gives ISO
// yyyy-mm-dd; we parse manually to dodge a per-tick `new Date()` allocation.
function formatTick(iso: string): string {
  const [, m, d] = iso.split('-');
  if (!m || !d) return iso;
  const months = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
  const idx = Number(m) - 1;
  return `${months[idx] ?? m} ${Number(d)}`;
}

interface OneChartProps {
  role: EngagementRole;
  series: EngagementPoint[];
}

const OneChart: React.FC<OneChartProps> = React.memo(({ role, series }) => {
  const { title, subject } = ROLE_LABELS[role];
  const colors = ROLE_COLORS[role];

  if (series.length === 0) {
    return (
      <div className="chart-card chart-card--empty">
        <h3 className="chart-card__title">{title}</h3>
        <p className="chart-card__sub">No sessions recorded for {subject.toLowerCase()} in this window yet.</p>
      </div>
    );
  }

  return (
    <div className="chart-card engagement-chart">
      <h3 className="chart-card__title">{title}</h3>
      <p className="chart-card__sub">
        How long {subject.toLowerCase()} take to come back vs how long a typical session lasts.
      </p>
      <ResponsiveContainer width="100%" height={260}>
        <LineChart data={series} margin={{ top: 8, right: 16, bottom: 8, left: 8 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#e5e7eb" />
          <XAxis
            dataKey="date"
            stroke="#475569"
            fontSize={11}
            tickFormatter={formatTick}
            minTickGap={24}
          />
          {/* Two y-axes: hours on the left, minutes on the right. The two
              metrics have wildly different scales (hours vs minutes) — sharing
              one axis would flatten the session-length line into noise. */}
          <YAxis
            yAxisId="ret"
            stroke={colors.ret}
            fontSize={11}
            tickFormatter={(v: number) => `${Math.round(v)}h`}
            width={42}
          />
          <YAxis
            yAxisId="usg"
            orientation="right"
            stroke={colors.usg}
            fontSize={11}
            tickFormatter={(v: number) => `${Math.round(v)}m`}
            width={42}
          />
          <Tooltip
            contentStyle={{ background: '#0f172a', border: 'none', color: '#fff', borderRadius: 8 }}
            labelFormatter={(label) => formatTick(String(label))}
            formatter={(value, name) => {
              if (value == null) return ['—', name];
              const n = Number(value);
              if (name === 'Return interval') return [`${n.toFixed(1)} h`, name];
              if (name === 'Avg session')     return [`${n.toFixed(1)} min`, name];
              return [String(value), name];
            }}
          />
          <Legend wrapperStyle={{ fontSize: 12 }} />
          <Line
            yAxisId="ret"
            type="monotone"
            dataKey="return_interval_hours"
            name="Return interval"
            stroke={colors.ret}
            strokeWidth={2}
            dot={false}
            connectNulls
            isAnimationActive={false}
          />
          <Line
            yAxisId="usg"
            type="monotone"
            dataKey="avg_usage_minutes"
            name="Avg session"
            stroke={colors.usg}
            strokeWidth={2}
            dot={false}
            connectNulls
            isAnimationActive={false}
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
});
OneChart.displayName = 'OneEngagementChart';

interface Props {
  token: string | null;
}

const EngagementCharts: React.FC<Props> = ({ token }) => {
  const [since, setSince] = useState<SinceWindow>('30d');
  const { data, isLoading, error } = useEngagement(token, since);

  // Slice per role with stable references so memoized children bail out
  // when an unrelated role's data shifts (rare, but cheap insurance).
  const slices = useMemo(() => {
    const d = data as EngagementResponse | undefined;
    return {
      user:  d?.roles.user?.series  ?? [],
      agent: d?.roles.agent?.series ?? [],
      staff: d?.roles.staff?.series ?? [],
    };
  }, [data]);

  return (
    <section className="engagement-section">
      <div className="engagement-section__head">
        <div>
          <h2 className="engagement-section__title">Engagement trends</h2>
          <p className="engagement-section__sub">
            Time between visits (left axis, hours) vs typical session length (right axis, minutes).
          </p>
        </div>
        <div className="chart-card__toggle engagement-section__toggle" role="tablist" aria-label="Time window">
          {SINCE_WINDOWS.map((w) => (
            <button
              key={w.value}
              type="button"
              role="tab"
              aria-selected={since === w.value}
              className={since === w.value ? 'is-on' : ''}
              onClick={() => setSince(w.value)}
            >
              {w.label}
            </button>
          ))}
        </div>
      </div>

      {isLoading && (
        <div className="chart-card chart-card--loading">Loading engagement data…</div>
      )}
      {error && !isLoading && (
        <div className="chart-card chart-card--error">Failed to load engagement data.</div>
      )}
      {!isLoading && !error && (
        <div className="engagement-section__grid">
          <OneChart role="user"  series={slices.user}  />
          <OneChart role="agent" series={slices.agent} />
          <OneChart role="staff" series={slices.staff} />
        </div>
      )}
    </section>
  );
};

export default EngagementCharts;
