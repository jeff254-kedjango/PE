/* ==========================================================================
   useFavorites Hook
   API-backed when authenticated; localStorage fallback for guests.
   On first authenticated load, any pending guest/local favorites are migrated
   to the backend so users keep what they had.

   Shared state: every call to useFavorites subscribes to a single module-level
   store, so a toggle in one component (FavoriteButton inside PropertyDetails)
   instantly updates Navbar, ProfilePage, and FavoritesPage without a reload.
   ========================================================================== */

import { useCallback, useEffect, useRef, useSyncExternalStore } from 'react';
import { useAuth } from '../context/AuthContext';
import { listFavorites, addFavorite, removeFavorite } from '../api/favorites';

const STORAGE_PREFIX = 'weespas_favorites_';
const GUEST_KEY = 'weespas_favorites_guest';

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
    /* Storage full or unavailable — degrade silently */
  }
}

/* ── Shared store ────────────────────────────────────────────────────────── */
type Listener = () => void;

let currentKey: string = GUEST_KEY;
let currentFavorites: string[] = loadLocal(GUEST_KEY);
const listeners = new Set<Listener>();

function notify(): void {
  listeners.forEach((l) => l());
}

function setCurrentKey(key: string): void {
  if (key === currentKey) return;
  currentKey = key;
  currentFavorites = loadLocal(key);
  notify();
}

function setFavoritesShared(ids: string[]): void {
  currentFavorites = ids;
  saveLocal(currentKey, ids);
  notify();
}

function getSnapshot(): string[] {
  return currentFavorites;
}

function subscribe(listener: Listener): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

/**
 * Membership-only subscription for a single property id.
 *
 * Use this inside hot-loop components (vertical-video feed cards, list rows)
 * where calling `useFavorites()` would re-render on every unrelated toggle —
 * the full hook's snapshot is the entire favorites array, so its identity
 * changes whenever ANY favorite is added/removed. This selector returns a
 * boolean, so React.useSyncExternalStore only re-renders the component when
 * THIS id's membership actually flips. Pair with `toggleFavoriteId` below to
 * avoid pulling in the full hook for action callbacks.
 */
export function useIsFavorite(id: string): boolean {
  return useSyncExternalStore(
    subscribe,
    () => currentFavorites.includes(id),
    () => currentFavorites.includes(id),
  );
}

/**
 * Fire-and-forget toggle that does not subscribe to favorites changes — safe
 * to call from components that don't need to re-render when the set changes.
 * Mirrors the logic of `useFavorites().toggleFavorite` but skips the hook
 * subscription overhead. Auth state is pulled lazily via `useAuth` so the
 * caller still gets per-user persistence.
 */
export function useToggleFavorite() {
  const { token, isAuthenticated } = useAuth();
  return useCallback(
    (id: string) => {
      const has = currentFavorites.includes(id);
      const next = has
        ? currentFavorites.filter((fid) => fid !== id)
        : [...currentFavorites, id];
      setFavoritesShared(next);
      if (isAuthenticated && token) {
        (has ? removeFavorite(token, id) : addFavorite(token, id)).catch(() => {
          /* leave optimistic state — next sync corrects */
        });
      }
    },
    [isAuthenticated, token],
  );
}

export function useFavorites() {
  const { user, token, isAuthenticated } = useAuth();
  const key = storageKey(user?.id);
  const migratedRef = useRef<string | null>(null);

  // Keep the shared store's key in sync with the active user.
  useEffect(() => {
    setCurrentKey(key);
  }, [key]);

  const favorites = useSyncExternalStore(subscribe, getSnapshot, getSnapshot);

  // Sync from backend (and migrate guest favorites) when auth state changes.
  useEffect(() => {
    let cancelled = false;
    if (isAuthenticated && token && user?.id) {
      (async () => {
        try {
          const remote = await listFavorites(token);
          const remoteIds = remote.map((f) => f.property_id);

          // One-time migration of guest/local favorites
          if (migratedRef.current !== user.id) {
            migratedRef.current = user.id;
            const guestIds = loadLocal(GUEST_KEY);
            const localIds = loadLocal(key);
            const pending = Array.from(new Set([...guestIds, ...localIds])).filter(
              (id) => !remoteIds.includes(id),
            );
            for (const id of pending) {
              try {
                await addFavorite(token, id);
                remoteIds.push(id);
              } catch {
                /* skip — likely deleted property */
              }
            }
            if (pending.length > 0) {
              localStorage.removeItem(GUEST_KEY);
            }
          }

          if (!cancelled) {
            setFavoritesShared(remoteIds);
          }
        } catch {
          if (!cancelled) setFavoritesShared(loadLocal(key));
        }
      })();
    } else {
      setFavoritesShared(loadLocal(key));
    }
    return () => {
      cancelled = true;
    };
  }, [isAuthenticated, token, user?.id, key]);

  const isFavorite = useCallback(
    (id: string) => favorites.includes(id),
    [favorites],
  );

  const toggleFavorite = useCallback(
    (id: string) => {
      const has = currentFavorites.includes(id);
      const next = has
        ? currentFavorites.filter((fid) => fid !== id)
        : [...currentFavorites, id];
      setFavoritesShared(next);
      if (isAuthenticated && token) {
        (has ? removeFavorite(token, id) : addFavorite(token, id)).catch(() => {
          /* leave optimistic state — next sync corrects */
        });
      }
    },
    [isAuthenticated, token],
  );

  return { favorites, isFavorite, toggleFavorite, favoriteCount: favorites.length };
}
