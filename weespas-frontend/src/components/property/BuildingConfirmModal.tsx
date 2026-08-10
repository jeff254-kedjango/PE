// BuildingConfirmModal — the owner's "which building is this?" dialog.
//
// Opens from the "Confirm your building" CTA (or the ?confirm=1 notification deep-link)
// when a listing's pin landed in a cluster the backend wouldn't auto-pick. Loads the
// candidate footprints (owner-only endpoint) and shows BuildingConfirmMap for a one-tap
// choice; on confirm it persists the link and the RiskPill flips to the real tier.
//
// Reuses the shared translucent overlay (.sf-modal-overlay) + portal idiom from
// StructuralFlagModal so styling and esc/backdrop behaviour stay consistent.
import React, { useEffect, useState } from 'react';
import { createPortal } from 'react-dom';
import Icon from '../ui/Icon';
import { useToast } from '../../context/ToastContext';
import BuildingConfirmMap from '../map/BuildingConfirmMap';
import {
  useListingCandidates,
  useConfirmListingBuilding,
} from '../../hooks/useListingCandidates';
import './BuildingConfirmModal.css';

interface BuildingConfirmModalProps {
  listingId: string;
  onClose: () => void;
}

const BuildingConfirmModal: React.FC<BuildingConfirmModalProps> = ({ listingId, onClose }) => {
  const { toast } = useToast();
  const { data, isLoading, isError } = useListingCandidates(listingId);
  const confirm = useConfirmListingBuilding(listingId);
  const [done, setDone] = useState(false);

  // Esc closes (unless a confirm is mid-flight).
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && !confirm.isPending) onClose();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose, confirm.isPending]);

  const handleConfirm = async (buildingId: number) => {
    try {
      await confirm.mutateAsync(buildingId);
      setDone(true);
      toast.success('Building confirmed — your listing now shows its monitored risk.', 4000);
      window.setTimeout(onClose, 1600);
    } catch (e) {
      toast.error(
        e instanceof Error ? e.message : 'Could not confirm. Please try again.',
        5000,
      );
    }
  };

  return createPortal(
    <div className="sf-modal-overlay" onClick={confirm.isPending ? undefined : onClose}>
      <div
        className="sf-modal bcm-modal"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-label="Confirm your building"
      >
        <div className="sf-modal__scroll">
          <header className="sf-modal__header">
            <div className="sf-modal__icon"><Icon name="mapPin" size={32} /></div>
            <h3 className="sf-modal__title">Confirm your building</h3>
          </header>

          {done ? (
            <div className="sf-modal__done">
              <div className="sf-modal__icon sf-modal__icon--success">
                <Icon name="check" size={32} />
              </div>
              <p className="sf-modal__copy">
                Thanks! Your listing is now matched to the right building and shows its
                monitored ground-risk reading.
              </p>
            </div>
          ) : (
            <>
              <p className="sf-modal__copy">
                Your pin is near a few buildings we monitor. Tap the one your listing is in
                so we show the right ground-movement reading. Colours show each building's
                current risk.
              </p>

              {isLoading && (
                <p className="bcm-modal__status" aria-busy="true">Loading nearby buildings…</p>
              )}
              {isError && (
                <p className="bcm-modal__status bcm-modal__status--err" role="alert">
                  Couldn't load nearby buildings. Please try again.
                </p>
              )}
              {!isLoading && !isError && (
                <BuildingConfirmMap
                  candidates={data?.candidates ?? []}
                  onConfirm={handleConfirm}
                  confirming={confirm.isPending}
                />
              )}

              <div className="sf-modal__actions">
                <button
                  type="button"
                  className="btn btn-secondary"
                  onClick={onClose}
                  disabled={confirm.isPending}
                >
                  Not now
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

export default BuildingConfirmModal;
