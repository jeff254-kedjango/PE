import { useEffect, useRef, useState } from "react";
import { WEESPAS_API, authHeaders, getTelemetryToken } from "./telemetry";

/**
 * Shops pinned onto the InSAR map (§8.1a — "the map IS the proof of proximity").
 *
 * The InSAR SPA is stateless and holds NO commerce token — only the Weespas-minted
 * telemetry token from the deep-link. So it asks WEESPAS (the token authority + BuildingLink
 * owner), not commerce, "which building footprints in this AOI are shops?". Weespas aggregates
 * the answer server-side (routers/insar.py GET /insar/shops/near) and mints the short-lived
 * read:feed commerce token itself. A shop's raw coordinates NEVER reach the browser: the pin
 * rides on `insar_building_id`, and the footprint the map already renders IS the location.
 *
 * INERT BY DEFAULT: no telemetry token (every anonymous / non-deep-link visit) ⇒ no fetch, no
 * pins. Mirrors telemetry.ts's posture — the shop layer is a signed-in-Weespas-user extra, and
 * its absence must never change how the base map behaves.
 *
 * NEVER THROWS: a subsidence map is a life-safety surface and must not go dark because commerce
 * is slow/down/inert. Any failure (network, non-OK, malformed, RS256-inert bridge) resolves to
 * an EMPTY shop list with `partial=true` — exactly the server's own degrade contract — so the
 * map renders identically to today with the shop layer simply absent.
 */

export interface ShopOnMap {
  property_uuid: string;
  insar_building_id: number;
  shop_id: string;
  name: string;
  category: string | null;
  /** PER-BUILDING ground-confirmed provenance (a recorded structural assessment exists on
   *  THIS footprint). Provenance, NOT a safety claim — same honest meaning as the shield. */
  confirmed: boolean;
}

export interface ShopsOnMap {
  shops: ShopOnMap[];
  /** True when the commerce read could not be completed and the shop layer is INCOMPLETE.
   *  The FE can surface a subtle "shops unavailable" hint; the map itself is unaffected. */
  partial: boolean;
}

const EMPTY: ShopsOnMap = { shops: [], partial: false };
// A degrade result is distinct from a genuine empty AOI (partial=true) so the caller can tell
// "no shops here" from "couldn't ask commerce". Shared singletons — the code only ever reads
// them (every populated result is a freshly-built object), so sharing is safe.
const DEGRADED: ShopsOnMap = { shops: [], partial: true };

/** Runtime-narrow one raw row from the aggregator into a ShopOnMap, or null if malformed.
 *  Defensive: a single bad row can never break the layer (matches the server's own filter). */
function parseShop(raw: unknown): ShopOnMap | null {
  if (typeof raw !== "object" || raw === null) return null;
  const r = raw as Record<string, unknown>;
  const propertyUuid = r.property_uuid;
  const buildingId = r.insar_building_id;
  const shopId = r.shop_id;
  const name = r.name;
  if (
    typeof propertyUuid !== "string" ||
    typeof buildingId !== "number" ||
    !Number.isFinite(buildingId) ||
    typeof shopId !== "string" ||
    typeof name !== "string"
  ) {
    return null;
  }
  return {
    property_uuid: propertyUuid,
    insar_building_id: buildingId,
    shop_id: shopId,
    name,
    category: typeof r.category === "string" ? r.category : null,
    confirmed: r.confirmed === true,
  };
}

/**
 * Fetch the shops for one AOI from the Weespas aggregator. Resolves to a ShopsOnMap; NEVER
 * rejects — any failure becomes the DEGRADED (empty + partial) result, which the hook simply
 * doesn't cache (so it's retried on the next AOI revisit).
 */
async function fetchShopsOnMap(aoiCode: string): Promise<ShopsOnMap> {
  // Inert without a telemetry token: an anonymous visitor never triggers a cross-origin call.
  if (!getTelemetryToken()) return EMPTY;
  try {
    const res = await fetch(
      `${WEESPAS_API}/insar/shops/near?aoi=${encodeURIComponent(aoiCode)}`,
      { headers: authHeaders() },
    );
    if (!res.ok) return DEGRADED;
    const body: unknown = await res.json();
    if (typeof body !== "object" || body === null) return DEGRADED;
    const b = body as Record<string, unknown>;
    const rawShops = Array.isArray(b.shops) ? b.shops : [];
    const shops = rawShops.map(parseShop).filter((s): s is ShopOnMap => s !== null);
    // Honour the server's own partial flag; treat a missing/odd value as complete (false).
    return { shops, partial: b.partial === true };
  } catch {
    // Any network/parse failure degrades — the map renders exactly as today, sans shop layer.
    return DEGRADED;
  }
}

/**
 * Shops for the active AOI, cached per AOI code (fetched at most once per page load; switching
 * AOIs is then a Map lookup). Mirrors useAoiBundle's cache + inflight-dedupe + abort shape.
 * Returns EMPTY while loading or for a null AOI — the caller renders no pins until data lands.
 */
export function useShopsOnMap(activeCode: string | null): ShopsOnMap {
  const cache = useRef(new Map<string, ShopsOnMap>());
  const inflight = useRef(new Map<string, Promise<ShopsOnMap>>());
  const [result, setResult] = useState<ShopsOnMap>(EMPTY);

  useEffect(() => {
    if (!activeCode) {
      setResult(EMPTY);
      return;
    }
    const cached = cache.current.get(activeCode);
    if (cached) {
      setResult(cached);
      return;
    }
    setResult(EMPTY); // clear stale pins from the previous AOI while this one loads

    let cancelled = false;
    let promise = inflight.current.get(activeCode);
    if (!promise) {
      promise = fetchShopsOnMap(activeCode).then(r => {
        // Cache only a completed answer — a degrade (commerce hiccup) must be retryable on the
        // next AOI revisit, not frozen in as "no shops here".
        if (!r.partial) cache.current.set(activeCode, r);
        inflight.current.delete(activeCode);
        return r;
      });
      inflight.current.set(activeCode, promise);
    }
    // No AbortController: the promise is SHARED via `inflight`, so aborting on unmount would
    // cancel a fetch another consumer (or a StrictMode remount) still needs. The `cancelled`
    // flag just discards a late result for a superseded AOI — matches useAoiBundle.
    promise.then(r => {
      if (!cancelled) setResult(r);
    });
    return () => { cancelled = true; };
  }, [activeCode]);

  return result;
}
