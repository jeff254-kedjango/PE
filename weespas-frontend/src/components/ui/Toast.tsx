// src/components/ui/Toast.tsx
// Renders the toast stack — positioned fixed at bottom-center.
// Each toast slides up on enter, slides down + fades on exit.
// Uses a two-phase removal: mark as "exiting" → wait for CSS animation → remove from DOM.

import React, { useState, useEffect, useCallback, useRef } from 'react';
import { useToast, type Toast as ToastType, type ToastVariant } from '../../context/ToastContext';
import Icon from './Icon';
import './Toast.css';

const ICON_MAP: Record<ToastVariant, React.ComponentProps<typeof Icon>['name']> = {
  success: 'check',
  error: 'x',
  warning: 'alertTriangle',
  info: 'info',
};

const EXIT_DURATION = 250; // matches CSS --toast-exit-duration

const ToastItem: React.FC<{ toast: ToastType; onDismiss: (id: string) => void }> = ({
  toast,
  onDismiss,
}) => {
  const [exiting, setExiting] = useState(false);
  const exitTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const handleDismiss = useCallback(() => {
    if (exiting) return;
    setExiting(true);
    exitTimer.current = setTimeout(() => onDismiss(toast.id), EXIT_DURATION);
  }, [exiting, onDismiss, toast.id]);

  // Cleanup on unmount (if parent removes before animation ends)
  useEffect(() => {
    return () => {
      if (exitTimer.current) clearTimeout(exitTimer.current);
    };
  }, []);

  return (
    <div
      className={`toast toast--${toast.variant}${exiting ? ' toast--exiting' : ''}`}
      role="status"
      aria-live="polite"
      aria-atomic="true"
    >
      <span className="toast__icon">
        <Icon name={ICON_MAP[toast.variant]} size={18} />
      </span>
      <p className="toast__message">{toast.message}</p>
      <button
        type="button"
        className="toast__close"
        onClick={handleDismiss}
        aria-label="Dismiss notification"
      >
        <Icon name="x" size={14} />
      </button>
      {toast.duration > 0 && (
        <div
          className="toast__progress"
          style={{ animationDuration: `${toast.duration}ms` }}
        />
      )}
    </div>
  );
};

const ToastContainer: React.FC = () => {
  const { toasts, dismiss } = useToast();

  if (toasts.length === 0) return null;

  return (
    <div className="toast-container" aria-label="Notifications">
      {toasts.map((t) => (
        <ToastItem key={t.id} toast={t} onDismiss={dismiss} />
      ))}
    </div>
  );
};

export default ToastContainer;
