// src/context/ToastContext.tsx
// Global toast notification system.
// Provides useToast() hook with toast(), toast.success(), toast.error(), etc.
// Manages queue, auto-dismiss, deduplication, and max visible limit.

import React, {
  createContext,
  useContext,
  useReducer,
  useCallback,
  useRef,
} from 'react';

// ── Public types ──

export type ToastVariant = 'success' | 'error' | 'warning' | 'info';

export interface ToastOptions {
  message: string;
  variant?: ToastVariant;
  duration?: number;       // ms — 0 = persistent (must dismiss manually)
  dedupe?: boolean;        // skip if identical message already visible
}

export interface Toast {
  id: string;
  message: string;
  variant: ToastVariant;
  duration: number;
  createdAt: number;
}

// ── Internal constants ──

const DEFAULT_DURATION = 3000;
const MAX_VISIBLE = 5;

let _nextId = 0;
function uid(): string {
  _nextId += 1;
  return `toast-${_nextId}-${Date.now()}`;
}

// ── Reducer ──

type Action =
  | { type: 'ADD'; toast: Toast }
  | { type: 'DISMISS'; id: string };

function reducer(state: Toast[], action: Action): Toast[] {
  switch (action.type) {
    case 'ADD': {
      const next = [...state, action.toast];
      // Evict oldest if over limit
      return next.length > MAX_VISIBLE ? next.slice(next.length - MAX_VISIBLE) : next;
    }
    case 'DISMISS':
      return state.filter((t) => t.id !== action.id);
    default:
      return state;
  }
}

// ── Context shape ──

interface ToastDispatch {
  (options: ToastOptions): string;
  success: (message: string, duration?: number) => string;
  error: (message: string, duration?: number) => string;
  warning: (message: string, duration?: number) => string;
  info: (message: string, duration?: number) => string;
}

interface ToastContextValue {
  toasts: Toast[];
  toast: ToastDispatch;
  dismiss: (id: string) => void;
  dismissAll: () => void;
}

const ToastContext = createContext<ToastContextValue | null>(null);

// ── Provider ──

export const ToastProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [toasts, dispatch] = useReducer(reducer, []);
  const timersRef = useRef<Map<string, ReturnType<typeof setTimeout>>>(new Map());

  const dismiss = useCallback((id: string) => {
    dispatch({ type: 'DISMISS', id });
    const timer = timersRef.current.get(id);
    if (timer) {
      clearTimeout(timer);
      timersRef.current.delete(id);
    }
  }, []);

  const dismissAll = useCallback(() => {
    timersRef.current.forEach((timer) => clearTimeout(timer));
    timersRef.current.clear();
    // Dismiss one-by-one so reducer stays pure
    toasts.forEach((t) => dispatch({ type: 'DISMISS', id: t.id }));
  }, [toasts]);

  const addToast = useCallback((options: ToastOptions): string => {
    const variant = options.variant ?? 'info';
    const duration = options.duration ?? DEFAULT_DURATION;

    // Dedupe: skip if an identical message + variant is already visible
    if (options.dedupe !== false) {
      const existing = toasts.find(
        (t) => t.message === options.message && t.variant === variant
      );
      if (existing) return existing.id;
    }

    const id = uid();
    const toast: Toast = {
      id,
      message: options.message,
      variant,
      duration,
      createdAt: Date.now(),
    };

    dispatch({ type: 'ADD', toast });

    if (duration > 0) {
      const timer = setTimeout(() => {
        dispatch({ type: 'DISMISS', id });
        timersRef.current.delete(id);
      }, duration);
      timersRef.current.set(id, timer);
    }

    return id;
  }, [toasts]);

  // Build the dispatch function with convenience methods
  const toastFn = useCallback(
    Object.assign(
      (options: ToastOptions) => addToast(options),
      {
        success: (message: string, duration?: number) =>
          addToast({ message, variant: 'success', duration }),
        error: (message: string, duration?: number) =>
          addToast({ message, variant: 'error', duration: duration ?? 5000 }),
        warning: (message: string, duration?: number) =>
          addToast({ message, variant: 'warning', duration }),
        info: (message: string, duration?: number) =>
          addToast({ message, variant: 'info', duration }),
      }
    ),
    [addToast]
  );

  return (
    <ToastContext.Provider value={{ toasts, toast: toastFn, dismiss, dismissAll }}>
      {children}
    </ToastContext.Provider>
  );
};

// ── Hook ──

export const useToast = (): ToastContextValue => {
  const ctx = useContext(ToastContext);
  if (!ctx) throw new Error('useToast must be used inside ToastProvider');
  return ctx;
};
