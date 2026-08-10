// StructuralFlagModal — the engineer/authority "second sensor" entry form.
//
// A certifier (professional engineer or authority; staff/admin too) records a
// structural judgement for the InSAR building behind a monitored listing. The InSAR
// build fuses it into the collapse score. AUTH_UNSAFE (authority condemnation) is
// offered ONLY to authority/staff/admin — the backend enforces this too; the UI just
// avoids presenting a doomed option.
//
// Overlay + panel + all inner chrome are fully self-contained in
// StructuralFlagModal.css: the translucent backdrop is .sf-modal-overlay and the
// panel chrome lives under the .sf-modal__* namespace. This modal (and the
// confirm-building one, which shares .sf-modal-overlay) opens from INSIDE the
// property-details panel, so its overlay sits on the --z-nested-modal tier (550),
// above the panel (--z-modal, 500). It deliberately does NOT reuse
// .adv-modal-overlay / .role-app-modal, whose CSS only ships in OTHER components'
// lazy chunks and is absent on the property page (relying on it left this form
// unstyled and behind the panel's map).
import React, { useEffect, useMemo, useState } from 'react';
import { createPortal } from 'react-dom';
import Icon from '../ui/Icon';
import { useAuth } from '../../context/AuthContext';
import { useToast } from '../../context/ToastContext';
import { isAuthority } from '../../utils/roles';
import { useCreateStructuralFlag } from '../../hooks/useStructuralFlag';
import {
  FLAG_CLEARED, FLAG_UNSAFE, FLAG_AUTH_UNSAFE,
  type FlagState, type FlagSource,
} from '../../api/structuralFlags';
import './StructuralFlagModal.css';

interface StructuralFlagModalProps {
  isOpen: boolean;
  listingId: string;
  aoiCode: string;
  insarBuildingId: number;
  onClose: () => void;
}

const STATE_OPTIONS: { value: FlagState; label: string; hint: string; authorityOnly?: boolean }[] = [
  { value: FLAG_CLEARED, label: 'Cleared', hint: 'Inspected and structurally sound.' },
  { value: FLAG_UNSAFE, label: 'Unsafe', hint: 'Engineer judgement: structurally unsafe.' },
  { value: FLAG_AUTH_UNSAFE, label: 'Condemned', hint: 'Authority condemnation / enforcement notice.', authorityOnly: true },
];

const StructuralFlagModal: React.FC<StructuralFlagModalProps> = ({
  isOpen, listingId, aoiCode, insarBuildingId, onClose,
}) => {
  const { user } = useAuth();
  const { toast } = useToast();
  const authority = isAuthority(user);
  const create = useCreateStructuralFlag(listingId);

  const [state, setState] = useState<FlagState>(FLAG_UNSAFE);
  const [observedAt, setObservedAt] = useState('');
  const [note, setNote] = useState('');
  const [done, setDone] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // An authority's judgement is authority-grade; a professional's is engineer-grade.
  // Source is derived from the role, never a free choice (matches the backend rule).
  const source: FlagSource = authority ? 'authority' : 'engineer';

  const options = useMemo(
    () => STATE_OPTIONS.filter((o) => !o.authorityOnly || authority),
    [authority],
  );

  useEffect(() => {
    if (!isOpen) {
      setState(FLAG_UNSAFE); setObservedAt(''); setNote(''); setDone(false); setError(null);
    }
  }, [isOpen]);

  useEffect(() => {
    if (!isOpen) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape' && !create.isPending) onClose(); };
    document.addEventListener('keydown', onKey);
    document.body.style.overflow = 'hidden';
    return () => { document.removeEventListener('keydown', onKey); document.body.style.overflow = ''; };
  }, [isOpen, onClose, create.isPending]);

  if (!isOpen) return null;

  const submit = async () => {
    setError(null);
    try {
      await create.mutateAsync({
        aoi_code: aoiCode,
        insar_building_id: insarBuildingId,
        state,
        source,
        observed_at: observedAt || null,
        note: note.trim() || null,
      });
      setDone(true);
      // Persist the acknowledgment as a toast too, so it survives the modal closing.
      toast.success(
        'Assessment recorded and under review. Your identity is linked to this record ' +
        'for follow-up and quality assurance. Thank you for keeping others safe. 🙏❤️',
        7000,
      );
      // Longer dwell so the certifier can read the full acknowledgment before it closes.
      window.setTimeout(onClose, 4200);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not record the flag. Please try again.');
    }
  };

  return createPortal(
    <div className="sf-modal-overlay" onClick={create.isPending ? undefined : onClose}>
      <div
        className="sf-modal"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-label="Record a structural judgement"
      >
        {/* Scroll lives on an INNER wrapper, not on .sf-modal: a scrollbar on the
            rounded panel itself squares off the corners on the scrollbar side. The
            panel clips with overflow:hidden so all four 15px corners stay rounded. */}
        <div className="sf-modal__scroll">
        <header className="sf-modal__header">
          <div className="sf-modal__icon"><Icon name="verified" size={32} /></div>
          <h3 className="sf-modal__title">Structural judgement</h3>
        </header>

        {done ? (
          <div className="sf-modal__done">
            <div className="sf-modal__icon sf-modal__icon--success">
              <Icon name="check" size={32} />
            </div>
            <p className="sf-modal__copy">
              We have recorded your assessment and are currently reviewing it for
              implementation. Please note that your identity will be linked to this record
              for future reference, follow-up, and quality assurance purposes.
            </p>
            <p className="sf-modal__copy sf-modal__thanks">
              Thank you for contributing to the safety and well-being of others today. 🙏❤️
            </p>
          </div>
        ) : (
          <>
            <p className="sf-modal__copy">
              Recording as <strong>{source === 'authority' ? 'an authority' : 'an engineer'}</strong> for
              building <code>#{insarBuildingId}</code> in <code>{aoiCode}</code>. This feeds the
              InSAR collapse score — InSAR can't see construction quality, so your judgement is the
              second sensor.
            </p>

            <fieldset className="sf-modal__states">
              <legend className="sf-modal__legend">Judgement</legend>
              {options.map((o) => (
                <label key={o.value} className={`sf-modal__state${state === o.value ? ' is-selected' : ''}`}>
                  <input
                    type="radio"
                    name="flag-state"
                    checked={state === o.value}
                    onChange={() => setState(o.value)}
                  />
                  <span className="sf-modal__state-label">{o.label}</span>
                  <span className="sf-modal__state-hint">{o.hint}</span>
                </label>
              ))}
            </fieldset>

            <label className="sf-modal__field">
              <span className="sf-modal__field-label">Date observed (optional)</span>
              <input
                type="date"
                className="sf-modal__input"
                value={observedAt}
                onChange={(e) => setObservedAt(e.target.value)}
              />
            </label>

            <label className="sf-modal__field">
              <span className="sf-modal__field-label">Note (optional)</span>
              <textarea
                className="sf-modal__textarea"
                rows={3}
                placeholder="What did you observe? (max 2000 chars)"
                value={note}
                onChange={(e) => setNote(e.target.value.slice(0, 2000))}
              />
            </label>

            {error && <p className="sf-modal__error" role="alert">{error}</p>}

            <div className="sf-modal__actions">
              <button type="button" className="btn btn-secondary" onClick={onClose} disabled={create.isPending}>
                Cancel
              </button>
              <button type="button" className="btn btn-primary" onClick={submit} disabled={create.isPending}>
                {create.isPending ? 'Recording…' : 'Record judgement'}
              </button>
            </div>
          </>
        )}
        </div>
      </div>
    </div>,
    document.body,
  );
};

export default StructuralFlagModal;
