import React from 'react';
import { useRiskSummary } from '../../hooks/useAnalytics';
import './analytics.css';
import './RiskTileCard.css';

interface Props {
  token: string | null;
}

/**
 * Risk-oversight tile for the Staff/Admin dashboard. Surfaces the catalog's InSAR
 * coverage mix and — the one that matters — the count of active listings sitting on a
 * building currently flagged UNSAFE / condemned. That join is sensitive (work_flow.md
 * §4.2/§9.7), so the backend is staff-gated and returns counts only; this tile shows
 * the number and routes oversight, never the raw listing↔flag map.
 */
const RiskTileCard: React.FC<Props> = ({ token }) => {
  const { data, isLoading, isError } = useRiskSummary(token);

  if (isError) {
    return (
      <section className="chart-card chart-card--error">
        <h3 className="chart-card__title">Risk oversight</h3>
        <p>Couldn't load risk data right now.</p>
      </section>
    );
  }

  const unsafe = data?.unsafe_listings ?? 0;
  const tiles = [
    { label: 'Monitored', value: data?.monitored ?? 0 },
    { label: 'Not monitored', value: data?.not_monitored ?? 0 },
    { label: 'Verifying', value: data?.pending ?? 0 },
    { label: 'Data unavailable', value: data?.unavailable ?? 0 },
  ];

  return (
    <section className="chart-card risk-tile">
      <h3 className="chart-card__title">Risk oversight</h3>
      <p className="chart-card__sub">
        How the live catalog maps onto the InSAR ground-monitoring footprints.
      </p>

      {/* The alarm line — listings on a building flagged unsafe/condemned. */}
      <div className={`risk-tile__alarm${unsafe > 0 ? ' risk-tile__alarm--hot' : ''}`}>
        <div className="risk-tile__alarm-value">{isLoading ? '…' : unsafe.toLocaleString()}</div>
        <div className="risk-tile__alarm-label">
          Active listings on an <strong>unsafe-flagged</strong> building
        </div>
      </div>

      <div className="analytics-summary-strip risk-tile__strip">
        {tiles.map((t) => (
          <div key={t.label} className="analytics-tile">
            <div className="analytics-tile__value">{isLoading ? '…' : t.value.toLocaleString()}</div>
            <div className="analytics-tile__label">{t.label}</div>
          </div>
        ))}
      </div>
    </section>
  );
};

export default RiskTileCard;
