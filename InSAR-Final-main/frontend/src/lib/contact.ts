import { WEESPAS_API, authHeaders, getTelemetryToken } from "./telemetry";

/**
 * §8.1b pair-radiate — the InSAR-side network client for the "we're connected" glow.
 *
 * Two halves, mirroring the backend (weespas routers/insar.py):
 *   - UPLINK  `postContact()`  — POST /insar/contact when a buyer opens a shop pin. Returns the
 *     buyer's OWN footprint ids in this AOI so the browser can glow them LOCALLY (consented by the
 *     tap, never via an SSE self-loop). Best-effort: any failure resolves to `null` and the click
 *     still works (the shop is still selected), it just doesn't add the pair glow.
 *   - DOWNLINK `subscribeContact()` — a long-lived SSE stream (GET /insar/contact/stream) over which
 *     the OTHER party's anonymized pulse arrives ({kind, shop_building_id, aoi} — never buyer ids).
 *
 * SSE via fetch + ReadableStream, NOT native EventSource: EventSource cannot send an Authorization
 * header, and putting the token in the URL would violate InSAR's token hygiene (telemetry.ts
 * strips ?wt precisely so the token never lingers in a URL). So we stream with a Bearer header,
 * exactly like every other InSAR data call.
 *
 * INERT BY DEFAULT: no telemetry token (every anonymous / non-deep-link visit) ⇒ postContact is a
 * no-op returning null and subscribeContact returns an immediate no-op unsubscribe — the map behaves
 * exactly as it does today. NEVER THROWS: a subsidence map is a life-safety surface; a commerce/bus
 * hiccup must never disturb it.
 */

/** How long a glow lasts when the trigger carries no TTL (the SSE seller-pulse path — the anonymized
 *  payload deliberately carries no timing). Matches the backend default (contact_glow_ttl_s=10). The
 *  buyer path uses the server-provided, clamped TTL from the POST response instead. */
export const CONTACT_GLOW_DEFAULT_MS = 10_000;

// Clamp bounds for a server-provided glow TTL — a buggy/hostile response can neither pin a glow
// on forever nor make it flicker sub-second.
const GLOW_MIN_MS = 1_000;
const GLOW_MAX_MS = 30_000;

/** The buyer's own footprints to glow + how long, from a POST /insar/contact response. */
export interface ContactResult {
  /** insar_building_ids of footprints the caller owns in this AOI (empty for a plain buyer). */
  own_building_ids: number[];
  /** Glow lifetime in ms, already normalized + clamped from the server's `glow_ttl_s`. */
  glow_ttl_ms: number;
  /** True once the anonymized pulse reached a live seller stream (diagnostic only — the buyer
   *  glow works regardless). */
  radiated: boolean;
}

/** An anonymized seller-side pulse: a viewer opened a pin on `shop_building_id` in `aoi`. Carries
 *  NO buyer identity or coordinates by design (privacy decision #2). */
export interface ContactEvent {
  shop_building_id: number;
  aoi: string;
}

/** Normalize + clamp a server `glow_ttl_s` (seconds) into a safe ms lifetime. */
function clampTtlMs(seconds: unknown): number {
  const s = typeof seconds === "number" && Number.isFinite(seconds) ? seconds : 0;
  const ms = s * 1000;
  if (ms <= 0) return CONTACT_GLOW_DEFAULT_MS;
  return Math.min(GLOW_MAX_MS, Math.max(GLOW_MIN_MS, ms));
}

/**
 * Register a pair-radiate contact (buyer opened a shop pin). Resolves to the buyer's own footprints
 * to glow + the glow TTL, or `null` on any failure / when inert (no token). NEVER rejects.
 */
export async function postContact(
  shopId: string,
  aoiCode: string,
  shopBuildingId: number,
): Promise<ContactResult | null> {
  if (!getTelemetryToken()) return null; // inert: anonymous visitors never trigger a cross-origin call
  try {
    const res = await fetch(`${WEESPAS_API}/insar/contact`, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...authHeaders() },
      body: JSON.stringify({ shop_id: shopId, aoi: aoiCode, shop_building_id: shopBuildingId }),
    });
    if (!res.ok) return null;
    const body: unknown = await res.json();
    if (typeof body !== "object" || body === null) return null;
    const b = body as Record<string, unknown>;
    const own = Array.isArray(b.own_building_ids)
      ? b.own_building_ids.filter((x): x is number => typeof x === "number" && Number.isFinite(x))
      : [];
    return { own_building_ids: own, glow_ttl_ms: clampTtlMs(b.glow_ttl_s), radiated: b.radiated === true };
  } catch {
    return null; // network/parse failure — the click still selected the shop, we just skip the glow
  }
}

// Reconnect policy for the SSE stream. The server caps a connection at contact_sse_max_connection_s
// and expects the client to transparently reconnect, so a clean end is NOT an error: we only grow
// the backoff for connections that failed fast (never reached "healthy"), so a hard-down endpoint
// isn't hammered while a normal hourly recycle reconnects promptly.
const RECONNECT_BASE_MS = 1_000;
const RECONNECT_MAX_MS = 30_000;
const HEALTHY_MS = 5_000;

/** Parse one SSE event block (lines split by "\n", block by a blank line) and, if it's a well-formed
 *  contact pulse, hand it to `onEvent`. Comments (": keep-alive" / ": connected") and malformed
 *  frames are ignored. */
function handleEvent(block: string, onEvent: (e: ContactEvent) => void): void {
  const dataLines: string[] = [];
  for (const line of block.split("\n")) {
    if (line.startsWith("data:")) dataLines.push(line.slice(5).replace(/^ /, ""));
  }
  if (dataLines.length === 0) return; // a comment / heartbeat, not a data event
  try {
    const obj: unknown = JSON.parse(dataLines.join("\n"));
    if (typeof obj !== "object" || obj === null) return;
    const o = obj as Record<string, unknown>;
    if (
      o.kind === "contact" &&
      typeof o.shop_building_id === "number" &&
      Number.isFinite(o.shop_building_id) &&
      typeof o.aoi === "string"
    ) {
      onEvent({ shop_building_id: o.shop_building_id, aoi: o.aoi });
    }
  } catch {
    /* malformed frame — ignore, never disturb the map */
  }
}

/**
 * Subscribe to this user's OWN pair-radiate channel as a long-lived SSE stream, invoking `onEvent`
 * for each anonymized seller pulse. Reconnects transparently across the server's connection cap.
 * Returns an unsubscribe fn (idempotent) — call it on unmount. Inert (returns a no-op) without a
 * telemetry token. NEVER THROWS.
 */
export function subscribeContact(onEvent: (e: ContactEvent) => void): () => void {
  if (!getTelemetryToken()) return () => {}; // inert for anonymous visitors
  const controller = new AbortController();
  let closed = false;
  let retry = 0;
  const sleep = (ms: number) => new Promise<void>((r) => setTimeout(r, ms));

  async function run(): Promise<void> {
    while (!closed) {
      const startedAt = Date.now();
      try {
        const res = await fetch(`${WEESPAS_API}/insar/contact/stream`, {
          headers: { ...authHeaders(), Accept: "text/event-stream" },
          signal: controller.signal,
        });
        // A rejected token is terminal — reconnecting would just 401 forever. Anything else is
        // transient (throw → reconnect with backoff).
        if (res.status === 401 || res.status === 403) return;
        if (!res.ok || !res.body) throw new Error(`contact stream ${res.status}`);
        const reader = res.body.getReader();
        const decoder = new TextDecoder();
        let buf = "";
        while (!closed) {
          const { value, done } = await reader.read();
          if (done) break; // server hit its connection cap → fall through to reconnect
          buf += decoder.decode(value, { stream: true }).replace(/\r\n/g, "\n");
          let sep: number;
          while ((sep = buf.indexOf("\n\n")) !== -1) {
            handleEvent(buf.slice(0, sep), onEvent);
            buf = buf.slice(sep + 2);
          }
        }
      } catch {
        if (closed) return; // aborted on unmount — expected, stop silently
      }
      if (closed) return;
      // Reset backoff after a healthy (long-lived) connection; grow it after a fast failure.
      retry = Date.now() - startedAt >= HEALTHY_MS ? 0 : retry + 1;
      const delay = Math.min(RECONNECT_MAX_MS, RECONNECT_BASE_MS * 2 ** retry);
      await sleep(delay + Math.floor(Math.random() * 250)); // jitter to avoid a reconnect thundering herd
    }
  }

  void run();
  return () => {
    closed = true;
    controller.abort();
  };
}
