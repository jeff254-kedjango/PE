// src/components/billing/ProScaleModal.tsx
//
// The §8 SOFT-GATE (commercial_model.md §7.2). Appears ONCE when a signed-in user's
// usage crosses the professional-scale threshold (decision === 'metered'). It is an
// upsell, NEVER a block and NEVER an accusation: the copy says "you're using Weespas at
// a professional scale", explains WHY (the signal breakdown), and offers a business
// plan. "See plans" files a `business_plan` contact inquiry (enterprise pricing is
// pilot-first / no public KES — commercial_model.md §8), so it's a warm lead, not a
// checkout. "Not now" simply dismisses; the mount-once gate won't show it again until
// the user crosses the threshold afresh.
//
// Reuses TierChooserModal's portal + overlay design language so the two billing modals
// feel like one family.
import React, { useEffect, useState } from 'react';
import { createPortal } from 'react-dom';
import Icon from '../ui/Icon';
import { submitContactForm } from '../../api/contact';
import type { PolicySignals } from '../../api/policy';
import type { User } from '../../types/auth';
import './ProScaleModal.css';

type Phase = 'idle' | 'sending' | 'sent' | 'failed';

interface Props {
  user: User | null;
  signals?: PolicySignals;
  onClose: () => void;
}

/** Human-readable reasons drawn from the score breakdown — so the ask is transparent
 *  ("here's what we noticed") rather than a black box. Only non-trivial signals show. */
function reasonsFrom(signals?: PolicySignals): string[] {
  if (!signals) return [];
  const out: string[] = [];
  if (signals.breadth > 1) out.push(`${signals.breadth} areas swept`);
  if (signals.volume > 0) out.push(`${signals.volume} buildings looked up`);
  if (signals.export_count > 0) out.push(`${signals.export_count} data exports`);
  if (signals.corporate_domain) out.push('a corporate email domain');
  return out;
}

const ProScaleModal: React.FC<Props> = ({ user, signals, onClose }) => {
  const [phase, setPhase] = useState<Phase>('idle');
  const busy = phase === 'sending';
  const reasons = reasonsFrom(signals);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape' && !busy) onClose(); };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [busy, onClose]);

  useEffect(() => {
    document.body.style.overflow = 'hidden';
    return () => { document.body.style.overflow = ''; };
  }, []);

  const onSeePlans = async () => {
    setPhase('sending');
    try {
      await submitContactForm({
        inquiry_purpose: 'business_plan',
        description: 'Professional-scale usage detected — business plan enquiry (soft-gate).',
        full_name: user?.name || undefined,
        email: user?.email || undefined,
        phone: user?.phone || undefined,
        message:
          'I would like to talk about a business / enterprise plan for Weespas Risk. '
          + (reasons.length ? `(Usage signals: ${reasons.join(', ')}.)` : ''),
      });
      setPhase('sent');
    } catch {
      setPhase('failed');
    }
  };

  return createPortal(
    <div className="proscale-overlay" onClick={busy ? undefined : onClose}>
      <div
        className="proscale"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-label="Business plan available"
      >
        {!busy && (
          <button className="proscale__close" onClick={onClose} aria-label="Close">
            <Icon name="x" size={18} />
          </button>
        )}

        <div className="proscale__head">
          <div className="proscale__icon"><Icon name="barChart" size={26} /></div>
          <h3 className="proscale__title">You&rsquo;re using Weespas at a professional scale</h3>
          <p className="proscale__sub">
            Looks like Weespas Risk is doing real work for you. We have a business plan
            built for teams &mdash; bulk lookups, data exports, portfolio monitoring, and a
            tamper-evident risk record for underwriting.
          </p>
        </div>

        {phase === 'sent' ? (
          <div className="proscale__status proscale__status--sent" role="status">
            <Icon name="check" size={18} />
            <span>Thanks &mdash; we&rsquo;ll reach out about a plan that fits. You can keep using Weespas as normal.</span>
          </div>
        ) : (
          <>
            {reasons.length > 0 && (
              <ul className="proscale__signals" aria-label="What we noticed">
                {reasons.map((r) => (
                  <li key={r} className="proscale__signal"><Icon name="check" size={13} /> {r}</li>
                ))}
              </ul>
            )}

            {phase === 'failed' && (
              <div className="proscale__status proscale__status--failed" role="status">
                <span>Couldn&rsquo;t send that just now &mdash; please try again.</span>
              </div>
            )}

            <button
              type="button"
              className="proscale__cta"
              onClick={onSeePlans}
              disabled={busy}
            >
              {busy && <span className="proscale__spinner" aria-hidden />}
              {busy ? 'Sending…' : phase === 'failed' ? 'Try again' : 'See business plans'}
            </button>
          </>
        )}

        <button type="button" className="proscale__dismiss" onClick={onClose}>
          {phase === 'sent' ? 'Close' : 'Not now'}
        </button>

        <p className="proscale__fineprint">
          Individuals always browse free. This is just an offer &mdash; nothing changes unless you choose it.
        </p>
      </div>
    </div>,
    document.body,
  );
};

export default ProScaleModal;
