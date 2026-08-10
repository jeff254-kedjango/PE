// Lightweight, idempotent "watched-a-short" tracker.
//
// Shorts drive high-conversion traffic, so every meaningful watch needs to
// land in the same analytics pipeline that powers the StatsPage / Agent
// dashboards. The backend already increments Property.view_count and writes
// a PropertyViewEvent when GET /properties/{id} is hit, so we ride that path
// instead of inventing a parallel events endpoint — one source of truth.
//
// Dedup: one view per property per browser session. Without this, lingering
// on a card (or scrubbing past it twice) would double-count and skew the
// view-rank ordering on the dashboard.

import { fetchPropertyDetails } from '../api/properties';

const SESSION_KEY = 'weespas_shorts_viewed';

function loadViewed(): Set<string> {
  try {
    const raw = sessionStorage.getItem(SESSION_KEY);
    if (!raw) return new Set();
    return new Set(JSON.parse(raw) as string[]);
  } catch {
    return new Set();
  }
}

function saveViewed(s: Set<string>): void {
  try {
    sessionStorage.setItem(SESSION_KEY, JSON.stringify([...s]));
  } catch {
    /* private mode / quota — counter still works in-memory until reload */
  }
}

let viewed: Set<string> | null = null;
const inflight = new Set<string>();

export function hasRecordedShortView(propertyId: string): boolean {
  if (!viewed) viewed = loadViewed();
  return viewed.has(propertyId);
}

/**
 * Record that a short reached the "watched" threshold for this property.
 *
 * Idempotent: subsequent calls in the same session are no-ops (also guarded
 * against in-flight duplicates while the request is on the wire).
 *
 * Fire-and-forget: callers don't await. Failure is silent — view-count is
 * best-effort signal, never blocking UX.
 */
export function recordShortView(propertyId: string): void {
  if (!viewed) viewed = loadViewed();
  if (viewed.has(propertyId) || inflight.has(propertyId)) return;
  inflight.add(propertyId);

  // GET /properties/:id is what the rest of the app uses on detail-open;
  // the backend treats both entry points the same for analytics.
  fetchPropertyDetails(propertyId)
    .then(() => {
      viewed!.add(propertyId);
      saveViewed(viewed!);
    })
    .catch(() => {
      /* network blip — don't poison the dedup set so a retry is possible */
    })
    .finally(() => {
      inflight.delete(propertyId);
    });
}
