// SellerModal — the shared portal shell for the seller-console forms (create shop / listing).
//
// Centralizes the modal mechanics the codebase repeats per-modal (createPortal to body, overlay
// click-to-close, Escape-to-close, body-scroll lock) so CreateShopForm/CreateListingForm only carry
// their fields. Closing is suppressed while `busy` (mid-submit) so a user can't dismiss an upload
// in flight. Matches the AddPropertyModal interaction contract.
//
// Backdrop-close is guarded against two ways it used to fire spuriously (the reported Boost "flicker"
// — open-then-instantly-close — was the taller Boost modal being dismissed by the tail of a
// double-click landing on its own overlay):
//   1. mousedown-origin: only close when the press BOTH started AND ended on the overlay itself
//      (a plain onClick={onClose} also closes when a drag/text-selection begun inside the dialog
//      releases over the backdrop — a real mis-dismissal, not just the flicker).
//   2. mount-ignore window: for a beat after opening, ignore overlay closes so the SAME gesture that
//      opened the modal (the second click of a double-click) can't immediately shut it.
import React, { useEffect, useRef } from 'react';
import { createPortal } from 'react-dom';
import './SellerModal.css';

// How long after open the overlay ignores close-clicks. Long enough to swallow a double-click's tail
// (typical inter-click gap ≤ ~250ms), short enough to never block a deliberate second interaction.
const OVERLAY_CLOSE_GRACE_MS = 300;

interface SellerModalProps {
  title: string;
  busy?: boolean;
  onClose: () => void;
  children: React.ReactNode;
  footer?: React.ReactNode;
}

const SellerModal: React.FC<SellerModalProps> = ({ title, busy, onClose, children, footer }) => {
  // True only while a mousedown landed on the overlay itself (not on the dialog). A backdrop close
  // requires the WHOLE press — down and up — to be on the overlay.
  const pressStartedOnOverlay = useRef(false);
  // Set once, OVERLAY_CLOSE_GRACE_MS after mount, after which overlay clicks may close.
  const closeArmed = useRef(false);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape' && !busy) onClose(); };
    document.addEventListener('keydown', onKey);
    document.body.style.overflow = 'hidden';
    const armTimer = window.setTimeout(() => { closeArmed.current = true; }, OVERLAY_CLOSE_GRACE_MS);
    return () => {
      document.removeEventListener('keydown', onKey);
      document.body.style.overflow = '';
      window.clearTimeout(armTimer);
    };
  }, [onClose, busy]);

  // Backdrop close: fires only when a real press begins AND ends on the overlay, and only after the
  // grace window (so the opening gesture's tail can't dismiss it). e.target === e.currentTarget keeps
  // clicks that bubbled up from the dialog (belt-and-braces with the child's stopPropagation) out.
  const onOverlayMouseDown = (e: React.MouseEvent) => {
    pressStartedOnOverlay.current = e.target === e.currentTarget;
  };
  const onOverlayClick = (e: React.MouseEvent) => {
    if (busy) return;
    if (!closeArmed.current) return;
    if (e.target !== e.currentTarget) return;
    if (!pressStartedOnOverlay.current) return;
    onClose();
  };

  return createPortal(
    <div className="seller-modal__overlay" onMouseDown={onOverlayMouseDown} onClick={onOverlayClick}>
      <div
        className="seller-modal"
        role="dialog"
        aria-modal="true"
        aria-label={title}
        onClick={(e) => e.stopPropagation()}
      >
        <header className="seller-modal__head">
          <h2>{title}</h2>
          <button
            type="button"
            className="seller-modal__close"
            aria-label="Close"
            disabled={busy}
            onClick={onClose}
          >×</button>
        </header>
        <div className="seller-modal__body">{children}</div>
        {footer && <footer className="seller-modal__foot">{footer}</footer>}
      </div>
    </div>,
    document.body,
  );
};

export default SellerModal;
