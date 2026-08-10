// FlashSaleChooser — launch / re-launch / clear a §8 flash sale on one listing.
//
// A flash sale is a nationwide, ≤1-hour "crazy offer": the seller sets a crazy price (a temporary
// override — the listing's normal price is untouched and reverts when the window closes) and a
// duration up to one hour. The platform ranks it against comparable shops by "craziness" (margin).
//
// The server rejects a non-discount, a bargain listing, or an out-of-bounds duration (422); we
// surface those via the toast. Duration presets are all ≤ 60 min (the 1-hour hard cap). If the
// listing already has a live flash sale we show its window + offer "Clear flash sale".
import React, { useState } from 'react';
import { useToast } from '../../../context/ToastContext';
import { useLaunchFlashSale, useClearFlashSale } from '../../../hooks/useReachMutations';
import type { CommerceSession, ListingOut } from '../../../api/commerce';
import SellerModal from './SellerModal';
import './ReachChooser.css';

interface FlashSaleChooserProps {
  session: CommerceSession | null;
  listing: ListingOut;
  onClose: () => void;
}

// All within the 1-hour hard cap (60 s .. 3600 s).
const DURATION_PRESETS: { label: string; seconds: number }[] = [
  { label: '15 minutes', seconds: 900 },
  { label: '30 minutes', seconds: 1800 },
  { label: '1 hour', seconds: 3600 },
];

const FlashSaleChooser: React.FC<FlashSaleChooserProps> = ({ session, listing, onClose }) => {
  const { toast } = useToast();
  // Price is entered in MAJOR units (e.g. KES 10) and converted to cents on submit (S9).
  const [priceMajor, setPriceMajor] = useState<string>('');
  const [seconds, setSeconds] = useState<number>(3600);
  const launch = useLaunchFlashSale(session, listing.id);
  const clear = useClearFlashSale(session, listing.id);
  const busy = launch.isPending || clear.isPending;

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    if (busy || !session) return;
    const major = Number(priceMajor);
    if (!Number.isFinite(major) || major <= 0) {
      toast.error('Enter a crazy price greater than zero.');
      return;
    }
    const flash_price_cents = Math.round(major * 100);
    launch.mutate(
      { flash_price_cents, duration_seconds: seconds },
      {
        onSuccess: () => { toast.success('Flash sale is live — going nationwide!'); onClose(); },
        onError: (err) => toast.error(err.message || 'Could not start the flash sale.'),
      },
    );
  };

  const onClear = () => {
    if (busy || !session) return;
    clear.mutate(undefined, {
      onSuccess: () => { toast.success('Flash sale cleared.'); onClose(); },
      onError: (err) => toast.error(err.message || 'Could not clear the flash sale.'),
    });
  };

  const expires = listing.flash_expires_at ? new Date(listing.flash_expires_at) : null;

  return (
    <SellerModal
      title="Flash sale"
      busy={busy}
      onClose={onClose}
      footer={
        <>
          {listing.is_flash_active && (
            <button type="button" className="seller-btn seller-btn--ghost" disabled={busy} onClick={onClear}
                    data-testid="flash-clear">
              Clear flash sale
            </button>
          )}
          <button type="submit" form="flash-form" className="seller-btn seller-btn--primary" disabled={busy}
                  data-testid="flash-submit">
            {launch.isPending ? 'Starting…' : listing.is_flash_active ? 'Re-launch' : 'Start flash sale'}
          </button>
        </>
      }
    >
      <form id="flash-form" onSubmit={submit} className="seller-form reach">
        <p className="reach__intro">
          A flash sale drops <strong>{listing.title}</strong> to a crazy price and shows it
          <strong> nationwide</strong> for up to an hour, ranked by how deep the discount is.
          {listing.is_flash_active && expires && (
            <> Currently live until <strong>{expires.toLocaleString()}</strong>.</>
          )}
        </p>

        <div className="seller-field">
          <label htmlFor="flash-price">Crazy price ({listing.currency})</label>
          <input
            id="flash-price"
            type="number"
            min="0"
            step="1"
            inputMode="decimal"
            value={priceMajor}
            disabled={busy}
            onChange={(e) => setPriceMajor(e.target.value)}
            placeholder="e.g. 10"
            data-testid="flash-price"
          />
          <small className="seller-field__hint">
            Must be below the going market price — that margin is your craziness score.
          </small>
        </div>

        <div className="seller-field">
          <label htmlFor="flash-duration">For how long</label>
          <select id="flash-duration" value={seconds} disabled={busy}
                  onChange={(e) => setSeconds(parseInt(e.target.value, 10))} data-testid="flash-duration">
            {DURATION_PRESETS.map((p) => <option key={p.seconds} value={p.seconds}>{p.label}</option>)}
          </select>
        </div>
      </form>
    </SellerModal>
  );
};

export default FlashSaleChooser;
