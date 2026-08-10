import React, { useMemo, useState } from 'react';
import {
  ResponsiveContainer, ScatterChart, Scatter, XAxis, YAxis, ZAxis,
  Tooltip, CartesianGrid, Cell,
} from 'recharts';
import { useAccessHeatmap, useInterestHeatmap } from '../../hooks/useAnalytics';
import type { HeatmapPoint, HeatmapResponse, SinceWindow } from '../../types/analytics';
import './analytics.css';

interface Props {
  token: string | null;
  since: SinceWindow;
}

type Mode = 'access' | 'interest';

const ACCESS_COLOR = '#059669';
const INTEREST_COLOR = '#84cc16';

function combinedNames(a: HeatmapPoint[], b: HeatmapPoint[]): string[] {
  const seen = new Set<string>();
  const names: string[] = [];
  for (const p of [...a, ...b]) {
    if (p.name && !seen.has(p.name)) {
      seen.add(p.name);
      names.push(p.name);
    }
  }
  return names.sort((x, y) => x.localeCompare(y));
}

function weightDomain(points: HeatmapPoint[]): [number, number] {
  if (points.length === 0) return [0, 1];
  return [0, Math.max(...points.map((p) => p.weight), 1)];
}

interface ChartProps {
  title: string;
  subtitle: string;
  data: HeatmapResponse | undefined;
  isLoading: boolean;
  error: unknown;
  color: string;
  names: string[];
  zDomain: [number, number];
  onPointClick: (p: HeatmapPoint, level: 'county' | 'city') => void;
}

const HeatmapScatter: React.FC<ChartProps> = ({
  title, subtitle, data, isLoading, error, color, names, zDomain, onPointClick,
}) => {
  if (isLoading) return <div className="chart-card chart-card--loading">Loading {title.toLowerCase()}…</div>;
  if (error) return <div className="chart-card chart-card--error">Failed to load {title.toLowerCase()}.</div>;

  const points = data?.points ?? [];
  const level = data?.level ?? 'county';
  const axisLabel = level === 'city' ? 'City' : 'County';

  // Map each point onto the shared categorical X axis (its name) so both
  // charts line up column-for-column. Y becomes the weight itself, which
  // gives a second visual cue alongside bubble size.
  const rows = points.map((p) => ({ ...p, x: p.name, y: p.weight }));

  return (
    <div className="chart-card">
      <h3 className="chart-card__title">{title}</h3>
      <p className="chart-card__sub">{subtitle}</p>
      {points.length === 0 ? (
        <p className="chart-card__empty">No {axisLabel.toLowerCase()} data yet.</p>
      ) : (
        <ResponsiveContainer width="100%" height={340}>
          <ScatterChart margin={{ top: 12, right: 16, bottom: 64, left: 8 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#e5e7eb" />
            <XAxis
              type="category"
              dataKey="x"
              name={axisLabel}
              allowDuplicatedCategory={false}
              ticks={names}
              interval={0}
              angle={-30}
              textAnchor="end"
              stroke="#475569"
              fontSize={11}
              height={64}
            />
            <YAxis
              type="number"
              dataKey="y"
              name="Weight"
              domain={[0, zDomain[1]]}
              stroke="#475569"
              fontSize={11}
              allowDecimals={false}
            />
            <ZAxis type="number" dataKey="weight" range={[80, 900]} domain={zDomain} name="Weight" />
            <Tooltip
              cursor={{ strokeDasharray: '3 3' }}
              contentStyle={{ background: '#0f172a', border: 'none', color: '#fff', borderRadius: 8 }}
              formatter={(_value, name, item) => {
                const r = (item?.payload ?? {}) as HeatmapPoint;
                if (name === 'Weight') return [r.weight.toLocaleString(), 'Weight'];
                if (name === axisLabel) return [r.name, axisLabel];
                return null;
              }}
              labelFormatter={(_, payload) => {
                const r = (payload?.[0]?.payload ?? {}) as HeatmapPoint;
                return r.name ?? '';
              }}
            />
            <Scatter
              data={rows}
              fill={color}
              onClick={(p: { payload?: HeatmapPoint }) => {
                if (p?.payload) onPointClick(p.payload, level);
              }}
            >
              {rows.map((_, i) => (
                <Cell key={i} cursor={level === 'county' ? 'pointer' : 'default'} fillOpacity={0.7} />
              ))}
            </Scatter>
          </ScatterChart>
        </ResponsiveContainer>
      )}
    </div>
  );
};

const HeatmapMap: React.FC<Props> = ({ token, since }) => {
  const [county, setCounty] = useState<string | undefined>(undefined);

  const accessQ = useAccessHeatmap(token, since, county);
  const interestQ = useInterestHeatmap(token, since, county);

  const accessPoints = accessQ.data?.points ?? [];
  const interestPoints = interestQ.data?.points ?? [];

  const names = useMemo(
    () => combinedNames(accessPoints, interestPoints),
    [accessPoints, interestPoints],
  );
  const accessDomain = useMemo<[number, number]>(
    () => weightDomain(accessPoints),
    [accessPoints],
  );
  const interestDomain = useMemo<[number, number]>(
    () => weightDomain(interestPoints),
    [interestPoints],
  );

  const handlePointClick = (p: HeatmapPoint, level: 'county' | 'city') => {
    if (level === 'county' && p.name) setCounty(p.name);
  };

  return (
    <div className="heatmap-map">
      <div className="heatmap-map__header">
        <div>
          <h3 className="heatmap-map__title">
            Geographic engagement {county ? <span className="heatmap-map__scope">— {county} (cities)</span> : <span className="heatmap-map__scope">(counties)</span>}
          </h3>
          <p className="heatmap-map__sub">
            X axis = {county ? 'city' : 'county'}, Y axis &amp; bubble size = weight.
            Compare where users <em>access</em> the app vs where listings draw <em>interest</em>.
            {!county && ' Click a county bubble to drill down to its cities.'}
          </p>
        </div>
        {county && (
          <button
            type="button"
            className="heatmap-map__reset"
            onClick={() => setCounty(undefined)}
          >
            ← Back to counties
          </button>
        )}
      </div>
      <div className="heatmap-map__grid">
        <HeatmapScatter
          title="Access"
          subtitle="Where users open the app from (sessions)"
          data={accessQ.data}
          isLoading={accessQ.isLoading}
          error={accessQ.error}
          color={ACCESS_COLOR}
          names={names}
          zDomain={accessDomain}
          onPointClick={handlePointClick}
        />
        <HeatmapScatter
          title="Interest"
          subtitle="Where listings draw views, favorites & inquiries"
          data={interestQ.data}
          isLoading={interestQ.isLoading}
          error={interestQ.error}
          color={INTEREST_COLOR}
          names={names}
          zDomain={interestDomain}
          onPointClick={handlePointClick}
        />
      </div>
    </div>
  );
};

export default HeatmapMap;
