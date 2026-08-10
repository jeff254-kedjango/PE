import React from 'react';
import type { ListingBenchmark } from '../../types/analytics';
import './analytics.css';

interface Props {
  benchmark: ListingBenchmark | undefined;
}

function tier(percentile: number): 'good' | 'warn' | 'bad' {
  if (percentile >= 0.75) return 'good';
  if (percentile >= 0.5) return 'warn';
  return 'bad';
}

function peerLabel(set: ListingBenchmark['peer_set']): string {
  switch (set) {
    case 'category_county_type': return 'category + county + type';
    case 'category_type': return 'category + type (countrywide)';
    case 'insufficient': return 'too few peers';
  }
}

const ListingBenchmarkCell: React.FC<Props> = ({ benchmark }) => {
  if (!benchmark || benchmark.percentile === null || benchmark.peer_set === 'insufficient') {
    return (
      <div className="benchmark-cell benchmark-cell--none" title="Not enough comparable peers">
        <span className="benchmark-cell__pill">—</span>
        <span className="benchmark-cell__sub">no peer set</span>
      </div>
    );
  }

  const t = tier(benchmark.percentile);
  const pctLabel = `p${Math.round(benchmark.percentile * 100)}`;
  const median = benchmark.peer_median_views ?? 0;

  return (
    <div
      className={`benchmark-cell benchmark-cell--${t}`}
      title={`Percentile against ${benchmark.peer_count} peers (${peerLabel(benchmark.peer_set)})`}
    >
      <span className="benchmark-cell__pill">{pctLabel}</span>
      <span className="benchmark-cell__sub">
        {benchmark.views} views · median {median}
      </span>
    </div>
  );
};

export default ListingBenchmarkCell;
