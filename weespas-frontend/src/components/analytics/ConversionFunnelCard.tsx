import React from 'react';
import { useAgentFunnel } from '../../hooks/useAnalytics';
import type { SinceWindow } from '../../types/analytics';
import './analytics.css';

interface Props {
  token: string | null;
  since: SinceWindow;
}

function pct(value: number | null): string {
  if (value === null || Number.isNaN(value)) return '—';
  return `${(value * 100).toFixed(1)}%`;
}

function delta(agent: number | null, platform: number | null): { label: string; tone: 'up' | 'down' | 'flat' } {
  if (agent === null || platform === null) return { label: '—', tone: 'flat' };
  const diff = agent - platform;
  if (Math.abs(diff) < 0.001) return { label: 'on par', tone: 'flat' };
  return {
    label: `${diff > 0 ? '▲' : '▼'} ${Math.abs(diff * 100).toFixed(1)}pp vs platform`,
    tone: diff > 0 ? 'up' : 'down',
  };
}

const ConversionFunnelCard: React.FC<Props> = ({ token, since }) => {
  const { data, isLoading, error } = useAgentFunnel(token, since);

  if (isLoading) return <div className="chart-card chart-card--loading">Loading funnel…</div>;
  if (error) return <div className="chart-card chart-card--error">Failed to load funnel.</div>;
  if (!data) return null;

  const { agent, platform } = data;
  if (!agent) {
    return (
      <div className="chart-card funnel-card">
        <h3 className="chart-card__title">Conversion funnel</h3>
        <p className="chart-card__empty">No agent profile linked to your account.</p>
      </div>
    );
  }

  const max = Math.max(agent.views, 1);
  const stages = [
    { key: 'views', label: 'Views', value: agent.views },
    { key: 'favorites', label: 'Favorites', value: agent.favorites },
    { key: 'inquiries', label: 'Inquiries', value: agent.inquiries },
  ];
  const v2f = delta(agent.view_to_fav, platform.view_to_fav);
  const f2i = delta(agent.fav_to_inq, platform.fav_to_inq);

  return (
    <div className="chart-card funnel-card">
      <h3 className="chart-card__title">Conversion funnel</h3>
      <p className="chart-card__sub">Views → Favorites → Inquiries, compared to platform averages.</p>

      <div className="funnel-card__stages">
        {stages.map((stage) => (
          <div key={stage.key} className="funnel-stage">
            <div className="funnel-stage__label">{stage.label}</div>
            <div className="funnel-stage__bar-track">
              <div
                className={`funnel-stage__bar funnel-stage__bar--${stage.key}`}
                style={{ width: `${Math.max(4, (stage.value / max) * 100)}%` }}
              >
                <span className="funnel-stage__count">{stage.value.toLocaleString()}</span>
              </div>
            </div>
          </div>
        ))}
      </div>

      <div className="funnel-card__rates">
        <div className="funnel-rate">
          <div className="funnel-rate__label">View → Favorite</div>
          <div className="funnel-rate__value">{pct(agent.view_to_fav)}</div>
          <div className="funnel-rate__platform">platform {pct(platform.view_to_fav)}</div>
          <div className={`funnel-rate__delta funnel-rate__delta--${v2f.tone}`}>{v2f.label}</div>
        </div>
        <div className="funnel-rate">
          <div className="funnel-rate__label">Favorite → Inquiry</div>
          <div className="funnel-rate__value">{pct(agent.fav_to_inq)}</div>
          <div className="funnel-rate__platform">platform {pct(platform.fav_to_inq)}</div>
          <div className={`funnel-rate__delta funnel-rate__delta--${f2i.tone}`}>{f2i.label}</div>
        </div>
      </div>
    </div>
  );
};

export default ConversionFunnelCard;
