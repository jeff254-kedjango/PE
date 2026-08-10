/* ==========================================================================
   useDismissals Hook
   API-backed when authenticated; localStorage fallback for guests.

   Shared module-level store so a dismiss in PropertyCard updates the list
   everywhere instantly.
   ========================================================================== */

import { useCallback, useEffect, useSyncExternalStore } from 'react';
import { useAuth } from '../context/AuthContext';
import { listDismissals, addDismissal, removeDismissal } from '../api/dismissals';

const STORAGE_PREFIX = 'weespas_dismissals_';
const GUEST_KEY = 'weespas_dismissals_guest';

function storageKey(userId: string | undefined): string {
  return userId ? `${STORAGE_PREFIX}${userId}` : GUEST_KEY;
}

function loadLocal(key: string): string[] {
  try {
    const stored = localStorage.getItem(key);
    return stored ? JSON.parse(stored) : [];
  } catch {
    return [];
  }
}

function saveLocal(key: string, ids: string[]): void {
  try {
    localStorage.setItem(key, JSON.stringify(ids));
  } catch {
    /* storage full — degrade silently */
  }
}

type Listener = () => void;

let currentKey: string = GUEST_KEY;
let currentDismissals: string[] = loadLocal(GUEST_KEY);
const listeners = new Set<Listener>();

function notify(): void {
  listeners.forEach((l) => l());
}

function setCurrentKey(key: string): void {
  if (key === currentKey) return;
  currentKey = key;
  currentDismissals = loadLocal(key);
  notify();
}

function setDismissalsShared(ids: string[]): void {
  currentDismissals = ids;
  saveLocal(currentKey, ids);
  notify();
}

function getSnapshot(): string[] {
  return currentDismissals;
}

function subscribe(listener: Listener): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function useDismissals() {
  const { user, token, isAuthenticated } = useAuth();
  const key = storageKey(user?.id);

  useEffect(() => {
    setCurrentKey(key);
  }, [key]);

  const dismissals = useSyncExternalStore(subscribe, getSnapshot, getSnapshot);

  useEffect(() => {
    let cancelled = false;
    if (isAuthenticated && token) {
      (async () => {
        try {
          const remote = await listDismissals(token);
          if (!cancelled) setDismissalsShared(remote.map((d) => d.property_id));
        } catch {
          if (!cancelled) setDismissalsShared(loadLocal(key));
        }
      })();
    } else {
      setDismissalsShared(loadLocal(key));
    }
    return () => {
      cancelled = true;
    };
  }, [isAuthenticated, token, key]);

  const isDismissed = useCallback(
    (id: string) => dismissals.includes(id),
    [dismissals],
  );

  const dismiss = useCallback(
    (id: string) => {
      if (currentDismissals.includes(id)) return;
      setDismissalsShared([...currentDismissals, id]);
      if (isAuthenticated && token) {
        addDismissal(token, id).catch(() => {
          /* leave optimistic state — next sync corrects */
        });
      }
    },
    [isAuthenticated, token],
  );

  const undismiss = useCallback(
    (id: string) => {
      if (!currentDismissals.includes(id)) return;
      setDismissalsShared(currentDismissals.filter((d) => d !== id));
      if (isAuthenticated && token) {
        removeDismissal(token, id).catch(() => {
          /* leave optimistic state */
        });
      }
    },
    [isAuthenticated, token],
  );

  return { dismissals, isDismissed, dismiss, undismiss };
}
