// useShopHeartbeat — pings a shop's /heartbeat endpoint every 30 seconds while mounted
// (§8, Chunk C; extended for C+ to carry viewing_listing_id + coarse coord).
//
// A stable-per-browser session_id is kept in localStorage so a reload counts as the SAME visit
// (upsert-by-session semantics on the server). A first mount fires an immediate ping; then every
// 30s while the tab is visible. Tab hidden → the interval keeps ticking (the server treats a
// stale heartbeat as "no longer live" after 60s, so a backgrounded tab correctly drops off the
// live count without any extra client logic).
//
// Signed-in visitors pass their `session` so the server captures their sub on the first ping.
// Anonymous visitors pass `null` — the server accepts unauthenticated heartbeats.
//
// §8 Chunk C+ additions:
//   * viewingListingId — the PDP the visitor is CURRENTLY on. Latest wins: every heartbeat
//     overwrites this on the server, including with null (leaving a PDP → null → seller stops
//     seeing 'viewing X'). The hook resends viewingListingId on EVERY tick so a stale ping
//     doesn't hold an old listing indefinitely.
//   * lastLat / lastLng — the visitor's coarse coord for the seller's area label (Kilimani /
//     CBD / etc.). Acquired ONCE per mount via navigator.geolocation.getCurrentPosition with
//     `enableHighAccuracy: false` (we want the fast IP-ish result, not a hardware fix). Denied /
//     unavailable → the field is omitted and the seller sees no area for this visitor.
//     Coords are cached in state and included with every subsequent ping.
//
// Fetch failures are swallowed: a lost heartbeat is not worth interrupting a browsing visitor.
import { useEffect, useRef, useState } from 'react';
import { postShopHeartbeat, type CommerceSession, type HeartbeatBodyExtras } from '../api/commerce';

/** localStorage key holding the browser's stable session id. Not identifying — the server
 *  treats it as an opaque bucketing token. */
const SESSION_STORAGE_KEY = 'weespas.shopViewSessionId';
/** Heartbeat interval. Matches the server's 60s "live" window (2x interval gives one dropped
 *  packet of slack). */
export const HEARTBEAT_INTERVAL_MS = 30_000;
/** Geolocation acquisition timeout. Deliberately short — a slow fix is worse than none (the
 *  card just shows no area for this viewer). */
const GEOLOCATION_TIMEOUT_MS = 8_000;
/** How long a cached coord may be reused before we ask the browser again. Long enough that
 *  we don't spam the browser API, short enough that a viewer who walks a block will refresh. */
const GEOLOCATION_MAX_AGE_MS = 5 * 60_000;

/** Fabricate an opaque, URL-safe id. Not cryptographically secure — it's a bucketing token,
 *  not authentication. 16 chars of base36 is enough entropy (~10^25) to make collisions
 *  practically impossible even across a very large user base. */
function makeSessionId(): string {
  const buf = new Uint8Array(12);
  if (typeof crypto !== 'undefined' && crypto.getRandomValues) {
    crypto.getRandomValues(buf);
  } else {
    // Fallback for exotic environments without crypto (SSR-in-jsdom, etc.). Non-crypto
    // strength is fine here — the session_id is opaque, not a secret.
    for (let i = 0; i < buf.length; i++) buf[i] = Math.floor(Math.random() * 256);
  }
  return Array.from(buf, (b) => b.toString(36).padStart(2, '0')).join('').slice(0, 24);
}

/** Get-or-create the stable browser session id. Lazy: only touches localStorage the first time
 *  it's called; subsequent calls read the cached value. */
export function getViewSessionId(): string {
  if (typeof window === 'undefined') return makeSessionId();   // SSR / test-env safety
  try {
    const existing = window.localStorage.getItem(SESSION_STORAGE_KEY);
    if (existing && existing.length > 0 && existing.length <= 64) return existing;
    const fresh = makeSessionId();
    window.localStorage.setItem(SESSION_STORAGE_KEY, fresh);
    return fresh;
  } catch {
    // localStorage denied (private mode on some browsers) → return an ephemeral id that
    // won't persist across reloads. The server still tracks it correctly within this session.
    return makeSessionId();
  }
}

interface UseShopHeartbeatArgs {
  session: CommerceSession | null;   // null → anonymous visitor
  shopId: string | null;             // null → hook is idle (waiting for the storefront to resolve)
  /** The commerce base URL. For a signed-in visitor this comes from `session.commerce_url`; for
   *  an anon visitor we can't mint a session, so the caller passes the URL through separately
   *  (typically resolved via the storefront lookup that fetched the shop). */
  commerceUrl: string | null;
  /** §8 Chunk C+. The listing the viewer is currently on (from a mounted PDP). Null when
   *  they're on the bare storefront index. Every heartbeat forwards the CURRENT value —
   *  navigating away from a PDP will send null on the next tick. */
  viewingListingId?: string | null;
  /** Overrideable in tests. Defaults to HEARTBEAT_INTERVAL_MS. */
  intervalMs?: number;
}

/** Fire heartbeats to a shop while mounted. Idle until both `shopId` and `commerceUrl` are set;
 *  a change to either restarts the schedule against the new target. */
export function useShopHeartbeat({
  session, shopId, commerceUrl, viewingListingId = null,
  intervalMs = HEARTBEAT_INTERVAL_MS,
}: UseShopHeartbeatArgs): void {
  // Ref so a re-render mid-flight doesn't spawn a duplicate interval; the effect owns the
  // timer's lifecycle and clears it on cleanup.
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // Coarse coord — acquired ONCE per mount, then reused for every subsequent ping. A viewer
  // who denies the prompt permanently has coords stay null (the seller sees no area for them,
  // which is the correct fallback). Kept in state so React re-runs the effect once the coord
  // resolves and the FIRST ping after the fix gets the coord attached; earlier pings just
  // omitted it and that's fine — the server's "latest wins" contract catches up.
  const [coords, setCoords] = useState<{ lat: number; lng: number } | null>(null);

  // Latest viewingListingId in a ref — the ping function can read it without needing to be
  // recreated (avoids re-arming the interval every time a PDP toggles).
  const listingRef = useRef<string | null>(viewingListingId ?? null);
  useEffect(() => { listingRef.current = viewingListingId ?? null; }, [viewingListingId]);

  // One-shot geolocation acquisition. Guarded so React StrictMode's double-mount doesn't
  // fire two prompts back-to-back. Errors are swallowed — a denied permission is a normal
  // path, not a bug.
  useEffect(() => {
    if (!shopId || !commerceUrl) return;
    if (typeof navigator === 'undefined' || !navigator.geolocation) return;
    if (coords !== null) return;
    let cancelled = false;
    try {
      navigator.geolocation.getCurrentPosition(
        (pos) => {
          if (cancelled) return;
          const { latitude, longitude } = pos.coords;
          if (typeof latitude === 'number' && typeof longitude === 'number') {
            setCoords({ lat: latitude, lng: longitude });
          }
        },
        () => { /* denied / timeout / position unavailable → stay null, seller sees no area */ },
        { enableHighAccuracy: false, timeout: GEOLOCATION_TIMEOUT_MS, maximumAge: GEOLOCATION_MAX_AGE_MS },
      );
    } catch {
      // Some browsers throw synchronously on a strict Permissions-Policy — swallow.
    }
    return () => { cancelled = true; };
  }, [shopId, commerceUrl, coords]);

  useEffect(() => {
    if (!shopId || !commerceUrl) return;

    const sessionId = getViewSessionId();
    const ping = () => {
      const extras: HeartbeatBodyExtras = { viewing_listing_id: listingRef.current };
      if (coords) {
        extras.last_lat = coords.lat;
        extras.last_lng = coords.lng;
      }
      postShopHeartbeat(session, shopId, sessionId, commerceUrl, extras).catch(() => {
        // Silently ignore — a dropped heartbeat is a normal event on a flaky network and the
        // server will drop the viewer off the live count automatically after LIVE_WINDOW.
      });
    };

    ping();   // immediate first ping so the seller sees the visitor without a 30s delay
    timerRef.current = setInterval(ping, intervalMs);

    return () => {
      if (timerRef.current !== null) {
        clearInterval(timerRef.current);
        timerRef.current = null;
      }
    };
  }, [session, shopId, commerceUrl, intervalMs, coords]);
}
