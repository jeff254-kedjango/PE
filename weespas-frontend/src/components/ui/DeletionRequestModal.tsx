import React, { useState, useEffect } from 'react';
import { createPortal } from 'react-dom';
import Icon from './Icon';
import './DeletionRequestModal.css';

interface DeletionRequestModalProps {
  isOpen: boolean;
  onClose: () => void;
  onSubmit: (reason: string) => void;
  targetName: string;
  isLoading?: boolean;
}

const DeletionRequestModal: React.FC<DeletionRequestModalProps> = ({
  isOpen,
  onClose,
  onSubmit,
  targetName,
  isLoading = false,
}) => {
  const [reason, setReason] = useState('');

  useEffect(() => {
    if (!isOpen) { setReason(''); return; }
    const handleKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && !isLoading) onClose();
    };
    document.addEventListener('keydown', handleKey);
    return () => document.removeEventListener('keydown', handleKey);
  }, [isOpen, onClose, isLoading]);

  useEffect(() => {
    if (isOpen) document.body.style.overflow = 'hidden';
    return () => { document.body.style.overflow = ''; };
  }, [isOpen]);

  if (!isOpen) return null;

  const canSubmit = reason.trim().length >= 10 && !isLoading;

  return createPortal(
    <div className="adv-modal-overlay" onClick={isLoading ? undefined : onClose}>
      <div
        className="deletion-request-modal"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-label="Request Deletion"
      >
        <div className="deletion-request-modal__icon">
          <Icon name="alertTriangle" size={40} />
        </div>
        <h3 className="deletion-request-modal__title">Request Deletion</h3>
        <p className="deletion-request-modal__message">
          Submit a deletion request for <strong>{targetName}</strong>. An admin will review and approve or reject this request.
        </p>
        <textarea
          className="deletion-request-modal__textarea"
          placeholder="Reason for deletion (required, min 10 characters)..."
          value={reason}
          onChange={(e) => setReason(e.target.value)}
          rows={3}
          disabled={isLoading}
          autoFocus
        />
        <div className="deletion-request-modal__actions">
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
            className="deletion-request-modal__submit"
            onClick={() => onSubmit(reason.trim())}
            disabled={!canSubmit}
          >
            <Icon name="alertTriangle" size={14} />
            {isLoading ? 'Submitting...' : 'Submit Request'}
          </button>
        </div>
      </div>
    </div>,
    document.body,
  );
};

export default DeletionRequestModal;
