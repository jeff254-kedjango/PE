// PromoteChooser — open / extend / clear a "selling now" window on one listing (§8 ephemerality).
//
// Two modes (the locked semantics): evergreen = the boost fades on expiry but the listing stays in
// the feed; story = the post disappears from the feed on expiry (stock untouched). A handful of
// duration presets (the service bounds 5 min..7 days; we only offer values inside that range). If
// the listing is already promoted we show its current window + offer "Clear promotion".
import React, { useState } from 'react';
import { useToast } from '../../../context/ToastContext';
import { usePromoteListing, useClearPromotion } from '../../../hooks/useReachMutations';
import type { CommerceSession, ListingOut, PromoMode } from '../../../api/commerce';
import SellerModal from './SellerModal';
import './ReachChooser.css';

interface PromoteChooserProps {
  session: CommerceSession | null;
  listing: ListingOut;
  onClose: () => void;
}

// Presets are all within the service bounds (300 s .. 604800 s).
const DURATION_PRESETS: { label: string; seconds: number }[] = [
  { label: '1 hour', seconds: 3600 },
  { label: '6 hours', seconds: 21600 },
  { label: '24 hours', seconds: 86400 },
  { label: '3 days', seconds: 259200 },
  { label: '7 days', seconds: 604800 },
];

const PromoteChooser: React.FC<PromoteChooserProps> = ({ session, listing, onClose }) => {
  const { toast } = useToast();
  const [mode, setMode] = useState<PromoMode>(listing.promo_mode ?? 'evergreen');
  const [seconds, setSeconds] = useState<number>(86400);
  const promote = usePromoteListing(session, listing.id);
  const clear = useClearPromotion(session, listing.id);
  const busy = promote.isPending || clear.isPending;

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    if (busy || !session) return;
    promote.mutate(
      { mode, duration_seconds: seconds },
      {
        onSuccess: () => { toast.success('Promotion is live — your listing is “selling now”.'); onClose(); },
        onError: (err) => toast.error(err.message || 'Could not start the promotion.'),
      },
    );
  };

  const onClear = () => {
    if (busy || !session) return;
    clear.mutate(undefined, {
      onSuccess: () => { toast.success('Promotion cleared.'); onClose(); },
      onError: (err) => toast.error(err.message || 'Could not clear the promotion.'),
    });
  };

  const expires = listing.promo_expires_at ? new Date(listing.promo_expires_at) : null;

  return (
    <SellerModal
      title="Selling now"
      busy={busy}
      onClose={onClose}
      footer={
        <>
          {listing.is_promoted && (
            <button type="button" className="seller-btn seller-btn--ghost" disabled={busy} onClick={onClear}>
              Clear promotion
            </button>
          )}
          <button type="submit" form="promote-form" className="seller-btn seller-btn--primary" disabled={busy}>
            {promote.isPending ? 'Starting…' : listing.is_promoted ? 'Update window' : 'Start promotion'}
          </button>
        </>
      }
    >
      <form id="promote-form" onSubmit={submit} className="seller-form reach">
        <p className="reach__intro">
          A “selling now” window highlights <strong>{listing.title}</strong> as actively on offer.
          {listing.is_promoted && expires && (
            <> Currently live until <strong>{expires.toLocaleString()}</strong>.</>
          )}
        </p>

        <fieldset className="reach__fieldset">
          <legend>How it ends</legend>
          <label className="reach__radio">
            <input type="radio" name="promo-mode" value="evergreen" checked={mode === 'evergreen'}
                   disabled={busy} onChange={() => setMode('evergreen')} data-testid="promo-mode-evergreen" />
            <span>
              <strong>Evergreen</strong>
              <em>The boost fades when the window ends — your listing stays in the feed.</em>
            </span>
          </label>
          <label className="reach__radio">
            <input type="radio" name="promo-mode" value="story" checked={mode === 'story'}
                   disabled={busy} onChange={() => setMode('story')} data-testid="promo-mode-story" />
            <span>
              <strong>Story</strong>
              <em>The post disappears from the feed when the window ends (your stock is untouched).</em>
            </span>
          </label>
        </fieldset>

        <div className="seller-field">
          <label htmlFor="promo-duration">For how long</label>
          <select id="promo-duration" value={seconds} disabled={busy}
                  onChange={(e) => setSeconds(parseInt(e.target.value, 10))} data-testid="promo-duration">
            {DURATION_PRESETS.map((p) => <option key={p.seconds} value={p.seconds}>{p.label}</option>)}
          </select>
        </div>
      </form>
    </SellerModal>
  );
};

export default PromoteChooser;
