// src/components/billing/TierChooserModal.tsx
//
// The subscription chooser that appears ONLY when a reveal returns 402 (the user
// clicked "Get directions" or a map pin and has no active window / quota left).
// It never shows on page load. Picking a tier fires an M-Pesa STK Push; the modal
// then waits for the PIN prompt to be confirmed (polling lives in RevealContext).
import React, { useEffect } from 'react';
import { createPortal } from 'react-dom';
import Icon from '../ui/Icon';
import type { Tier } from '../../api/billing';
import type { CheckoutPhase } from '../../context/RevealContext';
import './TierChooserModal.css';

interface Props {
  tiers: Tier[];
  phase: CheckoutPhase;
  message: string | null;
  onChoose: (tierCode: string) => void;
  onCancel: () => void;
}

function windowLabel(seconds: number): string {
  if (seconds % 86400 === 0) return `${seconds / 86400}h`.replace(/^24h$/, '24h');
  const hours = Math.round(seconds / 3600);
  return `${hours}h`;
}

const TierChooserModal: React.FC<Props> = ({ tiers, phase, message, onChoose, onCancel }) => {
  const busy = phase === 'initiating' || phase === 'pending';

  useEffect(() => {
    const handleKey = (e: KeyboardEvent) => { if (e.key === 'Escape' && !busy) onCancel(); };
    document.addEventListener('keydown', handleKey);
    return () => document.removeEventListener('keydown', handleKey);
  }, [busy, onCancel]);

  useEffect(() => {
    document.body.style.overflow = 'hidden';
    return () => { document.body.style.overflow = ''; };
  }, []);

  return createPortal(
    <div className="tier-chooser-overlay" onClick={busy ? undefined : onCancel}>
      <div className="tier-chooser" onClick={(e) => e.stopPropagation()} role="dialog" aria-label="Unlock locations">
        {!busy && (
          <button className="tier-chooser__close" onClick={onCancel} aria-label="Close">
            <Icon name="x" size={18} />
          </button>
        )}

        <div className="tier-chooser__head">
          <div className="tier-chooser__icon"><Icon name="mapPin" size={26} /></div>
          <h3 className="tier-chooser__title">Unlock exact locations</h3>
          <p className="tier-chooser__sub">
            Pay with M-Pesa to see precise pins and get directions. Choose a pack —
            it stays active for the whole window.
          </p>
        </div>

        {/* Pending / initiating / failed status panel */}
        {phase !== 'idle' && (
          <div className={`tier-chooser__status tier-chooser__status--${phase}`} role="status">
            {busy && <span className="tier-chooser__spinner" aria-hidden />}
            <span>{message}</span>
          </div>
        )}

        {/* Tier ladder — hidden while a payment is in flight so the user focuses on
            the phone prompt; shown again on failure so they can retry. */}
        {!busy && (
          <div className="tier-chooser__grid">
            {tiers.map((t) => (
              <button
                key={t.code}
                type="button"
                className="tier-card"
                onClick={() => onChoose(t.code)}
              >
                <span className="tier-card__price">KES {t.price_kes}</span>
                <span className="tier-card__locations">{t.locations} locations</span>
                <span className="tier-card__window">for {windowLabel(t.window_seconds)}</span>
              </button>
            ))}
          </div>
        )}

        {!busy && (
          <button type="button" className="tier-chooser__cancel" onClick={onCancel}>
            {phase === 'failed' ? 'Close' : 'Not now'}
          </button>
        )}
      </div>
    </div>,
    document.body,
  );
};

export default TierChooserModal;
