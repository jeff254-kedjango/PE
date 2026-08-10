import React from 'react';
import { useAgentRank } from '../../hooks/useAnalytics';
import type { SinceWindow } from '../../types/analytics';
import './analytics.css';

interface Props {
  token: string | null;
  since: SinceWindow;
}

const AgentRankCard: React.FC<Props> = ({ token, since }) => {
  const { data, isLoading, error } = useAgentRank(token, since);

  if (isLoading) {
    return <div className="chart-card chart-card--loading">Loading rank…</div>;
  }
  if (error) {
    return <div className="chart-card chart-card--error">Failed to load rank.</div>;
  }
  if (!data) return null;

  const { agent, platform, leaderboard } = data;
  const maxEpl = leaderboard.reduce((m, r) => Math.max(m, r.engagement_per_listing), 0) || 1;
  const inTop = agent ? leaderboard.some((r) => r.is_me && r.rank <= 20) : false;
  const tail = agent && !inTop ? leaderboard.filter((r) => r.is_me) : [];
  const mainList = leaderboard.filter((r) => r.rank <= 20);

  return (
    <div className="chart-card agent-rank-card">
      <h3 className="chart-card__title">Your rank</h3>
      <p className="chart-card__sub">
        Engagement per active listing — score = views + 3·favorites + 5·inquiries.
      </p>

      {agent ? (
        <div className="agent-rank-card__hero">
          <div className="agent-rank-card__big">
            #{agent.rank}<span className="agent-rank-card__of"> / {agent.total}</span>
          </div>
          <div className="agent-rank-card__meta">
            Top {Math.max(1, Math.round((1 - agent.percentile) * 100 || 0))}% —
            score {agent.engagement_per_listing.toFixed(1)} vs platform median {platform.p50.toFixed(1)} (p90 {platform.p90.toFixed(1)})
          </div>
        </div>
      ) : (
        <p className="chart-card__empty">No agent profile linked to your account.</p>
      )}

      {mainList.length === 0 ? (
        <p className="chart-card__empty">No leaderboard data yet.</p>
      ) : (
        <div className="agent-rank-card__list">
          {mainList.map((r) => (
            <div
              key={r.agent_id}
              className={`agent-rank-row${r.is_me ? ' agent-rank-row--me' : ''}`}
            >
              <span className="agent-rank-row__rank">#{r.rank}</span>
              <span className="agent-rank-row__name" title={r.name}>{r.name}</span>
              <span className="agent-rank-row__track">
                <span
                  className="agent-rank-row__fill"
                  style={{ width: `${Math.max(2, (r.engagement_per_listing / maxEpl) * 100)}%` }}
                />
              </span>
              <span className="agent-rank-row__epl">{r.engagement_per_listing.toFixed(1)}</span>
              <span className="agent-rank-row__listings">{r.active_listings} listings</span>
            </div>
          ))}
          {tail.length > 0 && (
            <div className="agent-rank-card__divider">…and you</div>
          )}
          {tail.map((r) => (
            <div key={r.agent_id} className="agent-rank-row agent-rank-row--me">
              <span className="agent-rank-row__rank">#{r.rank}</span>
              <span className="agent-rank-row__name" title={r.name}>{r.name}</span>
              <span className="agent-rank-row__track">
                <span
                  className="agent-rank-row__fill"
                  style={{ width: `${Math.max(2, (r.engagement_per_listing / maxEpl) * 100)}%` }}
                />
              </span>
              <span className="agent-rank-row__epl">{r.engagement_per_listing.toFixed(1)}</span>
              <span className="agent-rank-row__listings">{r.active_listings} listings</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
};

export default AgentRankCard;
