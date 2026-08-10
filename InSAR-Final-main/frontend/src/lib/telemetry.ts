/**
 * InSAR → Weespas commercial-usage telemetry (the metering half of the bridge).
 *
 * InSAR is a stateless public map with no identity of its own. When a SIGNED-IN
 * Weespas user opens it (via the "Risk Map" / "View on risk map" deep-link), Weespas
 * appends a short-lived telemetry-SCOPED token to the URL (`?wt=`). We read it once on
 * load, stash it in memory, and strip it from the address bar. Each building view /
 * CSV export then POSTs to Weespas's telemetry sink so the §8 company-detection scorer
 * can see portfolio-scale usage (commercial_model.md §7, work_flow.md §9.4).
 *
 * INERT BY DEFAULT: no `?wt` token (every anonymous / non-deep-link visit) ⇒ every
 * function here is a no-op. InSAR behaves exactly as it always has. This module imports
 * nothing from the app and never throws — a telemetry failure must never touch the map.
 */

// In-memory only (never localStorage): the token dies with the tab, which bounds a
// leaked token and matches InSAR's "no persistence" posture.
let _token: string | null = null;
let _aoi: string | null = null;
let _building: number | null = null;
// Where the "← Back to Weespas" chip should return the user. A SAME-ORIGIN RELATIVE
// path (e.g. "/properties/abc"), validated on parse — never an absolute URL, so a
// hand-crafted ?return= can't turn the chip into an off-site open redirect. Null when
// the visitor didn't arrive from a Weespas deep-link (chip stays hidden).
let _return: string | null = null;

// The access gate runs initTelemetryFromUrl() before RiskMap does, and the function
// strips the integration params from the URL on its first call — so a naive second call
// would see a clean URL and lose the deep-link target. Memoize the parsed result: the
// URL is read+stripped exactly once, and every caller (gate, then RiskMap) gets the same
// deep-link back. O(1) on every call after the first.
let _parsed: { aoi: string | null; building: number | null } | null = null;

// Distinct buildings already reported this session — dedupe so panning/clicking the
// same building doesn't inflate the volume signal. O(1) membership test.
const _viewed = new Set<number>();

// The Weespas API base. Defaults to the local dev backend, matching weespas-frontend's
// own config.ts. The real inert switch is the TOKEN (only present on an authed deep-link),
// so a default base is safe — without a token nothing is ever sent. Exported so the access
// gate (lib/access.ts) hits the same Weespas origin for its /insar/verify check.
export const WEESPAS_API = (import.meta.env.VITE_WEESPAS_API || "http://localhost:8000/api/v1").replace(/\/$/, "");

/** The telemetry-scoped token captured from the deep-link, or null on a tokenless load.
 *  The access gate reads this to decide whether the visitor may see the map at all. */
export function getTelemetryToken(): string | null {
  return _token;
}

/**
 * Authorization header for the InSAR DATA API (the bundle + AOI fetches). The data
 * endpoints are RS256-token-gated server-side (the data is the real asset, not just the
 * UI), so every data request must carry the same Weespas-minted token. Returns `{}` when
 * there is no token — the request then goes out unauthenticated and the server answers
 * 401, which the access gate has already pre-empted by bouncing tokenless visitors to
 * login. Spreadable straight into a fetch `headers` object.
 */
export function authHeaders(): Record<string, string> {
  return _token ? { Authorization: `Bearer ${_token}` } : {};
}

/**
 * Parse `?wt` (token), `?aoi`, `?building` from the URL exactly once, then strip them
 * via history.replaceState so the token doesn't linger in the address bar / history /
 * referrer. Returns the deep-link target (aoi/building) for the map to fly to.
 * Safe to call on a normal (param-less) load — returns nulls.
 */
export function initTelemetryFromUrl(): { aoi: string | null; building: number | null } {
  if (typeof window === "undefined") return { aoi: null, building: null };
  if (_parsed) return _parsed; // already read + stripped once — return the memoized target
  const params = new URLSearchParams(window.location.search);
  const wt = params.get("wt");
  const aoi = params.get("aoi");
  const building = params.get("building");
  const ret = params.get("return");

  if (wt) _token = wt;
  if (aoi) _aoi = aoi;
  if (building != null) {
    const n = Number(building);
    if (Number.isFinite(n)) _building = n;
  }
  if (ret) _return = sanitizeReturnPath(ret);

  // Strip the integration params from the URL (keep any others) so the token isn't
  // exposed in the visible URL or copied on share.
  if (wt || aoi || building != null || ret != null) {
    params.delete("wt");
    params.delete("aoi");
    params.delete("building");
    params.delete("return");
    const qs = params.toString();
    const url = window.location.pathname + (qs ? `?${qs}` : "") + window.location.hash;
    window.history.replaceState({}, "", url);
  }

  _parsed = { aoi: _aoi, building: _building };
  return _parsed;
}

/**
 * Validate a `?return=` value as a SAME-ORIGIN relative path before we trust it as the
 * back-chip target. The chip joins this onto InSAR's own configured Weespas origin, so
 * the only thing we must guarantee is that the path can't smuggle in a different origin:
 *  - must be a non-empty string starting with a single "/"
 *  - must NOT start with "//" or "/\" (protocol-relative → another host)
 *  - must NOT contain a scheme like "http:" / "javascript:" (defends decoded inputs)
 *  - control chars (newlines etc.) rejected
 * Returns the cleaned path, or null if it fails any check (chip then stays hidden).
 */
export function sanitizeReturnPath(raw: string): string | null {
  if (!raw || raw[0] !== "/") return null;        // must be an absolute-path reference
  if (raw[1] === "/" || raw[1] === "\\") return null; // "//host" / "/\host" → off-origin
  if (/[\x00-\x1f]/.test(raw)) return null;        // control chars
  if (/^\/[^?#]*:/.test(raw.slice(0, 64))) return null; // a scheme before any ?/# is suspect
  return raw;
}

/** The validated same-origin return path for the "← Back to Weespas" chip, or null when
 *  the visitor didn't arrive from a Weespas deep-link. Read after initTelemetryFromUrl(). */
export function getReturnPath(): string | null {
  return _return;
}

/** Absolute URL for the back-chip: the validated return path joined onto InSAR's OWN
 *  configured Weespas web origin (derived from VITE_WEESPAS_LOGIN_URL, falling back to
 *  the API origin). Null when there's no valid return path. Because the origin is ours
 *  and only the path comes from the URL, a tampered ?return= can't redirect off-site. */
export function getReturnUrl(): string | null {
  if (!_return) return null;
  return WEESPAS_WEB_ORIGIN + _return;
}

// The Weespas WEB origin (scheme://host[:port]) — distinct from WEESPAS_API (which is the
// /api/v1 base). It's the origin of the user-facing Weespas app, so the back-chip lands on
// a page, never the API server. Derived from VITE_WEESPAS_LOGIN_URL exactly as the access
// gate's LOGIN_URL is (same env var) — they must agree, or the chip would point somewhere
// the gate never sends users. DEV DEFAULT = :5174 (Weespas FE): the InSAR FE owns :5173 here
// (backend insar_public_url=5173), and Weespas runs on :5174 — pointing the chip at :5173
// would loop back into InSAR, not return to Weespas. We do NOT fall back to WEESPAS_API
// (:8000, the API) — "Back to Weespas" there yields a 404. Computed once at module load.
export const WEESPAS_WEB_ORIGIN = (() => {
  const loginUrl = import.meta.env.VITE_WEESPAS_LOGIN_URL || "http://localhost:5174/login";
  try {
    return new URL(loginUrl).origin;
  } catch {
    return "http://localhost:5174";
  }
})();

/** Fire-and-forget POST to the telemetry sink. No-op without a token; swallows all errors. */
function send(action: string, payload: Record<string, unknown>): void {
  if (!_token) return; // inert: only an authed Weespas deep-link carries a token
  // keepalive lets the POST survive a tab close (e.g. export-then-navigate).
  void fetch(`${WEESPAS_API}/insar-telemetry/event`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${_token}` },
    body: JSON.stringify({ action, ...payload }),
    keepalive: true,
  }).catch(() => {
    /* telemetry is best-effort — a failed beat must never disturb the map */
  });
}

/** Report a building view. Deduped per session so only DISTINCT buildings count. */
export function meterBuildingView(buildingId: number, aoiCode: string | null): void {
  if (!_token) return;
  if (_viewed.has(buildingId)) return;
  _viewed.add(buildingId);
  send("insar_building_view", { building_id: buildingId, aoi_code: aoiCode });
}

/** Report a CSV export of `count` rows for an AOI (the strongest company-tell). */
export function meterExport(aoiCode: string | null, count: number): void {
  send("insar_export", { aoi_code: aoiCode, count });
}
