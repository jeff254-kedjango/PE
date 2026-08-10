// src/context/RevealContext.tsx
//
// Single orchestrator for the paid listing-location reveal flow
// (PE/billing_architecture.md §4/§6). One public entry point:
//
//     const coords = await requestReveal(listingId);
//     if (coords) window.open(coords.directions_url, '_blank');
//
// `requestReveal` resolves with the exact coords whether they were already
// unlocked, revealed against an active window, or revealed AFTER the user paid
// through the chooser modal — the payment round-trip is handled internally and is
// invisible to callers. It resolves `null` if the user cancels or payment fails.
//
// NOTHING here runs on page load: the modal only opens when a reveal returns 402,
// which only happens because a user clicked "Get directions" or a map pin.
import React, {
  createContext, useCallback, useContext, useMemo, useRef, useState,
} from 'react';
import { useAuth } from './AuthContext';
import { useToast } from './ToastContext';
import {
  revealListing, startCheckout, pollCheckout, fetchEntitlement,
  type RevealSuccess, type Tier, type EntitlementStatus,
} from '../api/billing';
import TierChooserModal from '../components/billing/TierChooserModal';

export type CheckoutPhase = 'idle' | 'initiating' | 'pending' | 'failed';

interface RevealContextValue {
  /** Reveal a listing's exact location, paying through the chooser if needed.
   *  Resolves with the coords, or null if cancelled / failed / not logged in. */
  requestReveal: (listingId: string) => Promise<RevealSuccess | null>;
  /** Coords already revealed in this session (instant re-open, no new charge). */
  getRevealed: (listingId: string) => RevealSuccess | undefined;
  entitlement: EntitlementStatus | null;
}

const RevealContext = createContext<RevealContextValue | null>(null);

const POLL_INTERVAL_MS = 3000;
const POLL_TIMEOUT_MS = 75_000;   // ~ how long an STK prompt stays valid

export const RevealProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const { token, isAuthenticated } = useAuth();
  const { toast } = useToast();

  // Session cache of revealed coords (idempotent re-open is free server-side too).
  const revealedRef = useRef<Map<string, RevealSuccess>>(new Map());
  const [entitlement, setEntitlement] = useState<EntitlementStatus | null>(null);

  // Chooser modal state.
  const [chooserOpen, setChooserOpen] = useState(false);
  const [tiers, setTiers] = useState<Tier[]>([]);
  const [phase, setPhase] = useState<CheckoutPhase>('idle');
  const [phaseMessage, setPhaseMessage] = useState<string | null>(null);

  // The listing awaiting reveal once payment lands, + the promise resolver that
  // requestReveal() is blocked on.
  const pendingListingId = useRef<string | null>(null);
  const resolverRef = useRef<((v: RevealSuccess | null) => void) | null>(null);
  const pollTimer = useRef<ReturnType<typeof setInterval> | null>(null);
  const pollDeadline = useRef<number>(0);

  const clearPoll = useCallback(() => {
    if (pollTimer.current) { clearInterval(pollTimer.current); pollTimer.current = null; }
  }, []);

  const settle = useCallback((value: RevealSuccess | null) => {
    const resolve = resolverRef.current;
    resolverRef.current = null;
    pendingListingId.current = null;
    clearPoll();
    setChooserOpen(false);
    setPhase('idle');
    setPhaseMessage(null);
    if (resolve) resolve(value);
  }, [clearPoll]);

  const refreshEntitlement = useCallback(async (): Promise<EntitlementStatus | null> => {
    if (!token) return null;
    try {
      const st = await fetchEntitlement(token);
      setEntitlement(st);
      return st;
    } catch { return null; /* non-fatal */ }
  }, [token]);

  // --- the one public entry point -----------------------------------------
  const requestReveal = useCallback(
    async (listingId: string): Promise<RevealSuccess | null> => {
      // Cached? Hand it straight back — no network, no charge.
      const cached = revealedRef.current.get(listingId);
      if (cached) return cached;

      if (!isAuthenticated || !token) {
        toast.info('Log in to see the exact location and directions.');
        return null;
      }

      let result;
      try {
        result = await revealListing(token, listingId);
      } catch (e) {
        toast.error('Could not reveal this location. Please try again.');
        return null;
      }

      if (result.kind === 'revealed') {
        revealedRef.current.set(listingId, result);
        // If this newly-charged reveal landed on the free HOOK tier, tell the user
        // it was their free look (so the next one prompting payment isn't a surprise).
        if (result.newly_charged) {
          refreshEntitlement().then((st) => {
            if (st?.tier === 'HOOK') {
              toast.info('Here’s your free location. Unlock more anytime for a small fee.');
            }
          });
        } else {
          void refreshEntitlement();
        }
        return result;
      }

      // 402 → open the chooser and block until the user pays or cancels.
      setTiers(result.tiers ?? []);
      pendingListingId.current = listingId;
      setPhase('idle');
      setPhaseMessage(null);
      setChooserOpen(true);
      return new Promise<RevealSuccess | null>((resolve) => {
        resolverRef.current = resolve;
      });
    },
    [isAuthenticated, token, toast, refreshEntitlement],
  );

  // --- chooser actions ------------------------------------------------------
  const onCancel = useCallback(() => { settle(null); }, [settle]);

  const retryRevealAfterPayment = useCallback(async () => {
    const listingId = pendingListingId.current;
    if (!token || !listingId) { settle(null); return; }
    try {
      const r = await revealListing(token, listingId);
      if (r.kind === 'revealed') {
        revealedRef.current.set(listingId, r);
        void refreshEntitlement();
        toast.success('Location unlocked.');
        settle(r);
        return;
      }
    } catch { /* fall through to failure */ }
    // Paid but reveal still refused (rare race) — let the user retry.
    setPhase('failed');
    setPhaseMessage('Payment received, but the unlock did not apply. Tap retry.');
  }, [token, settle, refreshEntitlement, toast]);

  const onChoose = useCallback(
    async (tierCode: string) => {
      if (!token) { settle(null); return; }
      setPhase('initiating');
      setPhaseMessage('Sending the M-Pesa prompt to your phone…');
      let checkout;
      try {
        checkout = await startCheckout(token, tierCode);
      } catch {
        setPhase('failed');
        setPhaseMessage('Could not start the payment. Please try again.');
        return;
      }

      setPhase('pending');
      setPhaseMessage('Check your phone and enter your M-Pesa PIN to confirm.');
      clearPoll();
      pollDeadline.current = Date.now() + POLL_TIMEOUT_MS;

      pollTimer.current = setInterval(async () => {
        if (Date.now() > pollDeadline.current) {
          clearPoll();
          setPhase('failed');
          setPhaseMessage('Timed out waiting for payment. You can try again.');
          return;
        }
        let st;
        try { st = await pollCheckout(token, checkout.checkout_id); }
        catch { return; /* transient — keep polling until the deadline */ }

        if (st.status === 'paid') {
          clearPoll();
          setPhaseMessage('Payment confirmed — unlocking…');
          await retryRevealAfterPayment();
        } else if (st.status === 'failed' || st.status === 'expired') {
          clearPoll();
          setPhase('failed');
          setPhaseMessage('Payment was not completed. You can try again.');
        }
      }, POLL_INTERVAL_MS);
    },
    [token, settle, clearPoll, retryRevealAfterPayment],
  );

  const value = useMemo<RevealContextValue>(() => ({
    requestReveal,
    getRevealed: (id: string) => revealedRef.current.get(id),
    entitlement,
  }), [requestReveal, entitlement]);

  return (
    <RevealContext.Provider value={value}>
      {children}
      {chooserOpen && (
        <TierChooserModal
          tiers={tiers}
          phase={phase}
          message={phaseMessage}
          onChoose={onChoose}
          onCancel={onCancel}
        />
      )}
    </RevealContext.Provider>
  );
};

export const useReveal = (): RevealContextValue => {
  const ctx = useContext(RevealContext);
  if (!ctx) throw new Error('useReveal must be used inside RevealProvider');
  return ctx;
};
