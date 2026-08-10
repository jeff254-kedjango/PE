/* ==========================================================================
   RISK PILL — the honest InSAR coverage badge on a listing (work_flow.md §9.3 B)
   --------------------------------------------------------------------------
   Surfaces a listing's satellite-subsidence coverage as a small pill. The
   cardinal, life-safety rule (analysis_three.md §1, §7): 'unknown' / outside
   coverage / data-off is NEVER shown as 'safe'. So there is a distinct visual
   for not_monitored and unavailable — neither reads green.

   It is a SCREENING signal, not a structural-safety verdict (risk_model.md /
   analysis_two.md §5) — the title carries that disclaimer, and a 'nearest' /
   low-confidence match is labelled as approximate, never implied as precise.
   ========================================================================== */

import React from 'react';
import Icon from '../ui/Icon';
import type { ListingRisk } from '../../api/insar';
import './RiskPill.css';

/** Tier index → label + pill modifier. 0=STABLE … 4=CRITICAL (mirrors the InSAR
 *  danger_level; see scripts/postprocess.py DANGER_*). */
const TIERS: Record<number, { label: string; tone: string }> = {
  0: { label: 'Stable', tone: 'stable' },
  1: { label: 'Low movement', tone: 'low' },
  2: { label: 'Elevated', tone: 'elevated' },
  3: { label: 'High', tone: 'high' },
  4: { label: 'Critical', tone: 'critical' },
};

const SCREENING_NOTE =
  'Satellite subsidence screening — not a structural-safety verdict. Ground inspection required.';

interface RiskPillProps {
  risk: ListingRisk | undefined;
  isLoading?: boolean;
  isError?: boolean;
  /** True while the listing's footprint verification is still queued/running
   *  (Property.verification_status === 'pending'). Shows a distinct "Verifying…"
   *  state so a freshly-uploaded listing doesn't read as either safe or unknown. */
  isPending?: boolean;
}

const RiskPill: React.FC<RiskPillProps> = ({ risk, isLoading, isError, isPending }) => {
  // A listing whose background verification hasn't finished yet — honest "in progress",
  // distinct from both monitored and not_monitored.
  if (isPending) {
    return (
      <span className="risk-pill risk-pill--pending" aria-busy="true" title="We're checking this listing against our satellite monitoring map.">
        <span className="risk-pill__dot" />
        Verifying…
      </span>
    );
  }

  // While loading, render a quiet placeholder so layout doesn't jump.
  if (isLoading) {
    return (
      <span className="risk-pill risk-pill--loading" aria-busy="true" aria-label="Checking risk coverage">
        <span className="risk-pill__dot" />
        Checking coverage…
      </span>
    );
  }

  // A fetch error means we genuinely can't say — same honest stance as 'unavailable',
  // never a silent omission that could read as "fine".
  if (isError || !risk || risk.coverage === 'unavailable') {
    return (
      <span className="risk-pill risk-pill--unavailable" title="Risk data is temporarily unavailable.">
        <Icon name="info" size={13} />
        Risk data unavailable
      </span>
    );
  }

  if (risk.coverage === 'not_monitored') {
    return (
      <span
        className="risk-pill risk-pill--unmonitored"
        title="This location is outside our satellite monitoring coverage. Not monitored is not the same as safe."
      >
        <Icon name="mapPin" size={13} />
        Not monitored
      </span>
    );
  }

  // needs_confirmation — the pin landed in a cluster we won't auto-pick. We show the
  // CONSERVATIVE worst-case tier among the candidates, clearly labelled provisional, so
  // the listing is never under-stated while it waits for the owner to tap the right one.
  if (risk.coverage === 'needs_confirmation') {
    const worst = risk.danger_level != null ? TIERS[risk.danger_level] : undefined;
    return (
      <span
        className={`risk-pill risk-pill--provisional risk-pill--${worst?.tone ?? 'monitored'}`}
        title="Pending confirmation — showing the highest risk among the nearby buildings this listing could be. The owner can tap the exact building to confirm."
      >
        <Icon name="alertTriangle" size={13} />
        <span className="risk-pill__text">
          {worst ? `${worst.label} (nearby)` : 'Pending confirmation'}
          <span className="risk-pill__approx"> ·&nbsp;confirm</span>
        </span>
      </span>
    );
  }

  // monitored_land — a `land` listing: no building reading; ground is estimated from
  // nearby monitored buildings. Distinct visual; never a per-building tier.
  if (risk.coverage === 'monitored_land') {
    return (
      <span
        className="risk-pill risk-pill--land"
        title="Open land — ground movement estimated from nearby monitored buildings. Not a per-building reading."
      >
        <Icon name="mapPin" size={13} />
        Ground estimate (land)
      </span>
    );
  }

  // monitored — show the tier. Fall back to a neutral 'monitored' label if the tier
  // is missing (a monitored building with no scored level yet).
  const tier = risk.danger_level != null ? TIERS[risk.danger_level] : undefined;
  const approx = risk.match_method === 'nearest';

  return (
    <span
      className={`risk-pill risk-pill--monitored risk-pill--${tier?.tone ?? 'monitored'}`}
      title={SCREENING_NOTE + (approx ? ' Approximate — matched to the nearest monitored building.' : '')}
    >
      <Icon name="verified" size={13} />
      <span className="risk-pill__text">
        {tier ? tier.label : 'Monitored'}
        {approx && <span className="risk-pill__approx"> ·&nbsp;approx</span>}
      </span>
    </span>
  );
};

export default RiskPill;
