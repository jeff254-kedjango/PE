// SponsoredCapChooser — a seller applies for a per-shop OVERRIDE of the sponsored-lane fairness cap
// (§8.3 item 1). By default every shop may fill at most `default_cap` sponsored slots; a shop with
// unusually high genuine demand can request a higher ABSOLUTE cap that STAFF must approve.
//
// Honesty/anti-foot-gun contract kept in the copy:
//   * this affects only the LABELLED sponsored lane — never organic rank;
//   * the request is reviewed by staff (it is NOT self-granted);
//   * re-applying re-opens the request as pending, so an already-approved cap must be re-approved.
//
// Anti-drift (the Chunk-6 lesson): the ceiling (max_cap) and the global default (default_cap) come
// from the server status read — never hard-coded here. The status read is NON-DESTRUCTIVE (a GET),
// so merely opening this modal can't knock an approved override back to pending.
import React, { useState } from 'react';
import { useToast } from '../../../context/ToastContext';
import { useSponsoredCapStatus, useApplySponsoredCap } from '../../../hooks/useSponsoredCap';
import type { CommerceSession, ShopOut } from '../../../api/commerce';
import SellerModal from './SellerModal';
import './ReachChooser.css';
import './SponsoredCapChooser.css';

interface SponsoredCapChooserProps {
  session: CommerceSession | null;
  shop: ShopOut;
  onClose: () => void;
}

const SponsoredCapChooser: React.FC<SponsoredCapChooserProps> = ({ session, shop, onClose }) => {
  const { toast } = useToast();
  const { data: status, isLoading, isError } = useSponsoredCapStatus(session, shop.id);
  const apply = useApplySponsoredCap(session, shop.id);
  const busy = apply.isPending;

  // The seller's requested value. Seeded lazily from the server once loaded (below) so we never
  // hard-code a default; until then the field is empty and submit is disabled.
  const [requested, setRequested] = useState<string>('');

  const maxCap = status?.max_cap ?? null;
  const override = status?.override ?? null;
  const approvedActive = override?.status === 'approved' && (override.approved_cap ?? 0) > 0;

  // Parse + clamp on the client purely for UX (the service is the real authority): 1..max_cap.
  const parsed = Number.parseInt(requested, 10);
  const valid = Number.isFinite(parsed) && parsed >= 1 && (maxCap == null || parsed <= maxCap);

  const submit = () => {
    if (busy || !session || !valid) return;
    apply.mutate(parsed, {
      onSuccess: () => toast.success('Request sent — a reviewer will decide shortly.'),
      onError: (err) => toast.error(err.message || 'Could not send your request.'),
    });
  };

  const statusLine = (): React.ReactNode => {
    if (!override) return <>No request yet — your shop uses the standard cap.</>;
    if (override.status === 'pending') {
      return <>Requested <strong>{override.requested_cap}</strong> — awaiting review.</>;
    }
    if (override.status === 'approved' && (override.approved_cap ?? 0) > 0) {
      return <>Approved — up to <strong>{override.approved_cap}</strong> sponsored slots.</>;
    }
    return <>Your last request was not approved — your shop uses the standard cap.</>;
  };

  return (
    <SellerModal
      title="Sponsored cap"
      busy={busy}
      onClose={onClose}
      footer={<button type="button" className="seller-btn seller-btn--ghost" disabled={busy} onClick={onClose}>Done</button>}
    >
      <div className="reach">
        <p className="reach__intro">
          Your shop can fill a limited number of <em>sponsored</em> slots so one shop never floods a
          buyer’s feed. If <strong>{shop.name}</strong> has genuine demand, you can request a higher
          cap — it buys more <strong>reach, not rank</strong>, and a reviewer decides.
        </p>

        {isLoading && <p className="reach__state" role="status">Loading your cap…</p>}
        {isError && <p className="reach__state" role="alert">Couldn’t load your cap right now.</p>}

        {status && (
          <>
            <div className={`spcap__status spcap__status--${override?.status ?? 'none'}`} role="status" data-testid="spcap-status">
              {statusLine()}
              <span className="spcap__default">Standard cap: {status.default_cap} slots.</span>
            </div>

            <label className="spcap__field">
              <span className="spcap__label">Requested cap (max {maxCap})</span>
              <input
                type="number"
                min={1}
                max={maxCap ?? undefined}
                step={1}
                inputMode="numeric"
                value={requested}
                placeholder={`1–${maxCap}`}
                disabled={busy}
                onChange={(e) => setRequested(e.target.value)}
                data-testid="spcap-input"
              />
            </label>

            {approvedActive && (
              <p className="spcap__warn" role="note">
                Re-applying re-opens your request as <strong>pending</strong> — your current approved
                cap keeps working until a reviewer decides again.
              </p>
            )}

            <button
              type="button"
              className="seller-btn seller-btn--primary spcap__submit"
              disabled={busy || !valid}
              onClick={submit}
              data-testid="spcap-submit"
            >
              {busy ? 'Sending…' : 'Request review'}
            </button>
          </>
        )}
      </div>
    </SellerModal>
  );
};

export default SponsoredCapChooser;
