// BoostChooser — open a §8.3 Boost (paid-style REACH) on one listing.
//
// Honesty contract (must stay in the copy): a Boost buys a slot in a LABELLED sponsored lane —
// extra reach, NOT a higher organic rank. The organic feed stays pure/deterministic. Three tiers:
//   mtaa      — 10 km (the neighbourhood)
//   hustle    — 50 km (the wider hustle area)
//   sovereign — nationwide
// Each tier has a small number of FREE daily chances; we show live remaining/cap from
// useBoostAllowances and disable a tier at remaining===0 (so a click can't eat a 429). On success
// the allowance refetches (count visibly decrements) and we hold the grant so the seller can stop
// it early — there is no "list my boosts" endpoint, so revoke is offered for the just-opened grant.
import React, { useState } from 'react';
import { useToast } from '../../../context/ToastContext';
import { useBoostAllowances } from '../../../hooks/useBoostAllowances';
import { useBoostTiers } from '../../../hooks/useBoostTiers';
import { useCreateBoost, useRevokeBoost } from '../../../hooks/useReachMutations';
import type {
  CommerceSession, ListingOut, BoostTier, BoostGrantOut, BoostTierMetaOut,
} from '../../../api/commerce';
import SellerModal from './SellerModal';
import './ReachChooser.css';

interface BoostChooserProps {
  session: CommerceSession | null;
  listing: ListingOut;
  onClose: () => void;
}

// Brand flavour only — the concrete, DRIFT-PRONE facts (reach km, free cap, price) come from the
// server catalogue (GET /boosts/tiers) so the FE and backend config can never disagree. The name
// and the "area" descriptor are UX copy, not numbers, so they stay here.
const TIER_BRAND: Record<BoostTier, { name: string; area: string }> = {
  mtaa: { name: 'Mtaa', area: 'your neighbourhood' },
  hustle: { name: 'Hustle', area: 'the wider area' },
  sovereign: { name: 'Sovereign', area: 'the whole country' },
};

// Server-authoritative reach text: nationwide when there's no radius, else the exact km the backend
// configured for this tier (never a hard-coded "10 km").
const reachText = (meta: BoostTierMetaOut): string =>
  meta.radius_m == null
    ? `Nationwide — ${TIER_BRAND[meta.tier].area}`
    : `${Math.round(meta.radius_m / 1000)} km — ${TIER_BRAND[meta.tier].area}`;

const BoostChooser: React.FC<BoostChooserProps> = ({ session, listing, onClose }) => {
  const { toast } = useToast();
  const { data: catalogue, isLoading: tiersLoading } = useBoostTiers(session);
  const { data: allowances, isLoading: allowancesLoading } = useBoostAllowances(session);
  const createBoost = useCreateBoost(session);
  const revokeBoost = useRevokeBoost(session);
  const [grant, setGrant] = useState<BoostGrantOut | null>(null);
  const busy = createBoost.isPending || revokeBoost.isPending;
  const isLoading = tiersLoading || allowancesLoading;

  const remainingFor = (tier: BoostTier): number | null => {
    const row = allowances?.tiers.find((t) => t.tier === tier);
    return row ? row.remaining : null;
  };
  const capFor = (tier: BoostTier): number | null => {
    const row = allowances?.tiers.find((t) => t.tier === tier);
    return row ? row.daily_cap : null;
  };

  const boost = (tier: BoostTier) => {
    if (busy || !session) return;
    createBoost.mutate(
      { target_type: 'listing', target_id: listing.id, tier },
      {
        onSuccess: (g) => { setGrant(g); toast.success(`Boosted — ${TIER_BRAND[tier].name} reach is live.`); },
        onError: (err) => toast.error(err.message || 'Could not start the boost.'),
      },
    );
  };

  const stop = () => {
    if (busy || !session || !grant) return;
    revokeBoost.mutate(grant.id, {
      onSuccess: () => { toast.success('Boost stopped.'); setGrant(null); },
      onError: (err) => toast.error(err.message || 'Could not stop the boost.'),
    });
  };

  return (
    <SellerModal
      title="Boost reach"
      busy={busy}
      onClose={onClose}
      footer={<button type="button" className="seller-btn seller-btn--ghost" disabled={busy} onClick={onClose}>Done</button>}
    >
      <div className="reach">
        <p className="reach__intro">
          A Boost puts <strong>{listing.title}</strong> in a clearly-labelled <em>sponsored</em> slot
          to more nearby buyers. It buys <strong>reach, not rank</strong> — the ordinary feed stays
          fair. You get a few free boosts a day per tier.
        </p>

        {isLoading && <p className="reach__state" role="status">Loading your daily boosts…</p>}

        {grant && (
          <div className="reach__active" role="status">
            <span><strong>{TIER_BRAND[grant.tier].name}</strong> boost is live.</span>
            <button type="button" className="seller-btn seller-btn--ghost" disabled={busy} onClick={stop} data-testid="boost-stop">
              Stop boost
            </button>
          </div>
        )}

        <ul className="reach__tiers">
          {(catalogue?.tiers ?? []).map((meta) => {
            const tier = meta.tier;
            const remaining = remainingFor(tier);
            const cap = capFor(tier);
            const spent = remaining === 0;
            return (
              <li key={tier} className={`reach__tier${spent ? ' reach__tier--spent' : ''}`}>
                <div className="reach__tier-info">
                  <strong>{TIER_BRAND[tier].name}</strong>
                  <em>{reachText(meta)}</em>
                  {meta.price_kes > 0 && (
                    <span className="reach__tier-price" data-testid={`boost-price-${tier}`}>
                      KES {meta.price_kes.toLocaleString()}
                    </span>
                  )}
                  {remaining != null && cap != null && (
                    <span className="reach__tier-quota" data-testid={`boost-remaining-${tier}`}>
                      {remaining} of {cap} free left today
                    </span>
                  )}
                </div>
                <button
                  type="button"
                  className="seller-btn seller-btn--primary"
                  disabled={busy || spent || remaining == null}
                  onClick={() => boost(tier)}
                  data-testid={`boost-tier-${tier}`}
                >
                  {spent ? 'None left' : 'Boost'}
                </button>
              </li>
            );
          })}
        </ul>
      </div>
    </SellerModal>
  );
};

export default BoostChooser;
