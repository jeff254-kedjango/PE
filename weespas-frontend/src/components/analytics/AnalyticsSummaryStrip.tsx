import React from 'react';
import { useAnalyticsSummary } from '../../hooks/useAnalytics';
import type { SinceWindow } from '../../types/analytics';
import './analytics.css';

interface Props {
  token: string | null;
  since: SinceWindow;
}

const AnalyticsSummaryStrip: React.FC<Props> = ({ token, since }) => {
  const { data, isLoading } = useAnalyticsSummary(token, since);

  const tiles = [
    { label: 'Sessions', value: data?.sessions ?? 0 },
    { label: 'Property views', value: data?.views ?? 0 },
    { label: 'Searches', value: data?.searches ?? 0 },
    { label: 'Favorites', value: data?.favorites ?? 0 },
    { label: 'Inquiries', value: data?.inquiries ?? 0 },
  ];

  return (
    <div className="analytics-summary-strip">
      {tiles.map((t) => (
        <div key={t.label} className="analytics-tile">
          <div className="analytics-tile__value">{isLoading ? '…' : t.value.toLocaleString()}</div>
          <div className="analytics-tile__label">{t.label}</div>
        </div>
      ))}
    </div>
  );
};

export default AnalyticsSummaryStrip;
