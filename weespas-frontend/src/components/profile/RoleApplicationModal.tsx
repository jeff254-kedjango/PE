// RoleApplicationModal — three states inside one component:
//
//   1. "form"        — eligible applicant, textarea + Submit
//   2. "ineligible"  — staff-applicant who doesn't meet the threshold;
//                      shows the canned two-paragraph copy + a progress
//                      hint computed from the eligibility precompute.
//   3. "done"        — success state shown for 1.6s, then auto-closes.
//
// Re-uses the portal/overlay shell from DeletionRequestModal so visual
// chrome (overlay tint, body-scroll lock, Escape-to-close) stays
// consistent. No new modal infrastructure.
import React, { useEffect, useState } from 'react';
import { createPortal } from 'react-dom';
import Icon from '../ui/Icon';
import type { RoleApplicationRole, StaffStats } from '../../api/roleApplications';
import './RoleApplicationModal.css';

type Mode = 'form' | 'ineligible' | 'done';

interface RoleApplicationModalProps {
  isOpen: boolean;
  role: RoleApplicationRole;
  /** Pass when role==='staff' so we can render the progress hint. */
  staffStats?: StaffStats | null;
  /** True iff the user can submit. Computed by the caller. */
  isEligible: boolean;
  isLoading?: boolean;
  errorMessage?: string | null;
  onClose: () => void;
  onSubmit: (message: string) => Promise<void> | void;
}

const TITLE: Record<RoleApplicationRole, string> = {
  agent: 'Become an Agent',
  staff: 'Become Staff',
};

// Canned copy — verbatim from the spec. Two paragraphs separated by a
// blank line.
const STAFF_INELIGIBLE_LINES = [
  'You must be active for atleast 3-months, with 10 Listings and 500views ( Aproximately 5 unique viewers per day ) to be eligible to apply as staff.PLEASE NOTE - We recruit top talent on a rolling basis and we are always aware of top performance',
  'Check your agent ranking stats to see your performance . Staff members receive allowances and salary when approved.',
];

const RoleApplicationModal: React.FC<RoleApplicationModalProps> = ({
  isOpen,
  role,
  staffStats,
  isEligible,
  isLoading = false,
  errorMessage,
  onClose,
  onSubmit,
}) => {
  const [message, setMessage] = useState('');
  const [mode, setMode] = useState<Mode>('form');

  // Compute the initial mode whenever the modal opens. We never flip
  // back to "ineligible" once the user is mid-form — the eligibility
  // value is taken at open-time.
  useEffect(() => {
    if (!isOpen) {
      setMessage('');
      setMode('form');
      return;
    }
    if (role === 'staff' && !isEligible) {
      setMode('ineligible');
    } else {
      setMode('form');
    }
  }, [isOpen, role, isEligible]);

  // Escape-to-close — disabled mid-submit so a stray keypress doesn't
  // discard the user's draft while the network is in flight.
  useEffect(() => {
    if (!isOpen) return;
    const handle = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && !isLoading) onClose();
    };
    document.addEventListener('keydown', handle);
    return () => document.removeEventListener('keydown', handle);
  }, [isOpen, onClose, isLoading]);

  // Body-scroll lock while open.
  useEffect(() => {
    if (isOpen) document.body.style.overflow = 'hidden';
    return () => { document.body.style.overflow = ''; };
  }, [isOpen]);

  if (!isOpen) return null;

  const canSubmit = message.trim().length >= 10 && !isLoading;

  const handleSubmit = async () => {
    if (!canSubmit) return;
    try {
      await onSubmit(message.trim());
      setMode('done');
      // Auto-close after a short success state.
      window.setTimeout(onClose, 1600);
    } catch {
      // Error is surfaced via the errorMessage prop the parent passes.
    }
  };

  return createPortal(
    <div className="adv-modal-overlay" onClick={isLoading ? undefined : onClose}>
      <div
        className="role-app-modal"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-label={TITLE[role]}
      >
        <header className="role-app-modal__header">
          <div className="role-app-modal__icon">
            <Icon name={role === 'agent' ? 'user' : 'verified'} size={32} />
          </div>
          <h3 className="role-app-modal__title">{TITLE[role]}</h3>
        </header>

        {mode === 'ineligible' && (
          <>
            {staffStats && (
              <ul className="role-app-modal__stats" aria-label="Your progress">
                <li>
                  <span className="role-app-modal__stat-label">Months as agent</span>
                  <span className="role-app-modal__stat-value">
                    {Math.floor(staffStats.days / 30)} / {Math.floor(staffStats.min_days / 30)}
                  </span>
                </li>
                <li>
                  <span className="role-app-modal__stat-label">Active listings</span>
                  <span className="role-app-modal__stat-value">
                    {staffStats.listings} / {staffStats.min_listings}
                  </span>
                </li>
                <li>
                  <span className="role-app-modal__stat-label">Total views</span>
                  <span className="role-app-modal__stat-value">
                    {staffStats.views} / {staffStats.min_views}
                  </span>
                </li>
              </ul>
            )}
            {STAFF_INELIGIBLE_LINES.map((line, i) => (
              <p key={i} className="role-app-modal__copy">{line}</p>
            ))}
            <div className="role-app-modal__actions">
              <button
                type="button"
                className="role-app-modal__submit"
                onClick={onClose}
                autoFocus
              >
                OK
              </button>
            </div>
          </>
        )}

        {mode === 'form' && (
          <>
            <p className="role-app-modal__copy">
              {role === 'agent'
                ? 'Tell us about yourself and why you would like to be listed as a Weespas agent. Our admins will review your application.'
                : 'Tell us why you would like to join the Weespas staff team. Our admins will review your application.'}
            </p>
            <textarea
              className="role-app-modal__textarea"
              placeholder="Write your Application (min 10 characters)…"
              value={message}
              onChange={(e) => setMessage(e.target.value.slice(0, 1000))}
              rows={5}
              disabled={isLoading}
              autoFocus
            />
            <div className="role-app-modal__footer-row">
              <span className="role-app-modal__count" aria-live="polite">
                {message.length} / 1000
              </span>
            </div>
            {errorMessage && (
              <p className="role-app-modal__error" role="alert">{errorMessage}</p>
            )}
            <div className="role-app-modal__actions">
              <button
                type="button"
                className="adv-modal__clear"
                onClick={onClose}
                disabled={isLoading}
              >
                Cancel
              </button>
              <button
                type="button"
                className="role-app-modal__submit"
                onClick={handleSubmit}
                disabled={!canSubmit}
              >
                {isLoading ? 'Submitting…' : 'Submit'}
              </button>
            </div>
          </>
        )}

        {mode === 'done' && (
          <div className="role-app-modal__done">
            <div className="role-app-modal__icon role-app-modal__icon--success">
              <Icon name="check" size={32} />
            </div>
            <p className="role-app-modal__copy">
              Application received. Our admins will review and notify you.
            </p>
          </div>
        )}
      </div>
    </div>,
    document.body,
  );
};

export default RoleApplicationModal;
