// InSAR bridge API client.
//
// Mints a short-lived, telemetry-scoped token from Weespas and builds the deep-link
// URL that opens the (separate, stateless) InSAR risk-map SPA. The token lets InSAR
// report commercial-usage events back into Weespas's metering spine WITHOUT InSAR
// having any login of its own (see PE/work_flow.md §9, commercial_model.md §7).
//
// Mirrors the idiom in src/api/roleApplications.ts: `fetchJson` + Bearer header.
import { fetchJson, API_BASE_URL } from './config';

/**
 * Same-origin path to the Weespas login, carrying the intent to continue into InSAR.
 * InSAR is free but login-required now (commercial_model.md): an anonymous "Risk Map"
 * click can't open the map (InSAR would just bounce it), so we send the user to sign in
 * first and resume the map afterwards. `listing` is preserved so a "View on risk map"
 * deep-link still flies to that building once they're back. See resumeInsarAfterLogin().
 */
export function loginThenInsarUrl(listingId?: string): string {
  const params = new URLSearchParams({ next: 'insar' });
  if (listingId) params.set('listing', listingId);
  return `/login?${params.toString()}`;
}

const authHeaders = (token: string) => ({ Authorization: `Bearer ${token}` });

export interface InsarSession {
  token: string;            // telemetry-scoped JWT, passed to InSAR as ?wt=
  insar_url: string;        // base URL of the InSAR SPA
  aoi_code?: string | null; // set when a listing resolved to a monitored building
  building_id?: number | null;
}

/** Mint a telemetry session + deep-link target. Pass a `listingId` to resolve that
 *  listing's InSAR building (so the map can fly to it); omit it for a nav-level open. */
export async function getInsarSession(token: string, listingId?: string): Promise<InsarSession> {
  const qs = listingId ? `?listing_id=${encodeURIComponent(listingId)}` : '';
  return fetchJson<InsarSession>(`${API_BASE_URL}/insar/session-token${qs}`, {
    headers: authHeaders(token),
  });
}

/** The honest coverage of a listing (work_flow.md §9.3 Option B). 'unknown' is NEVER
 *  'safe' — the backend reports the state explicitly:
 *    monitored          — resolved to one building we stand behind (carries a tier)
 *    needs_confirmation — pin landed in a cluster; a worst-case PROVISIONAL tier shows
 *                         until the owner taps the right building
 *    monitored_land     — `land` listing: ground estimated from neighbours, no building tier
 *    not_monitored      — outside coverage / no match (never "safe")
 *    unavailable        — InSAR DB off at read time (re-checkable, never final) */
export type InsarCoverage =
  | 'monitored'
  | 'needs_confirmation'
  | 'monitored_land'
  | 'not_monitored'
  | 'unavailable';

export interface ListingRisk {
  coverage: InsarCoverage;
  danger_level: number | null;   // 0=STABLE … 4=CRITICAL; worst-case when provisional
  aoi_code: string | null;
  insar_building_id: number | null;  // resolved footprint id (when monitored)
  match_method: string | null;   // 'pip' | 'nearest' | 'disambiguated' | 'agent_confirmed' | 'link'
  match_confidence: number | null;
  // True ⇒ danger_level is a conservative placeholder for an unconfirmed clustered pin.
  provisional?: boolean;
  // How many candidate buildings the pin could be (drives the "confirm" prompt).
  candidate_count?: number | null;
}

/** One footprint a clustered pin could be — for the tap-to-confirm map. Geometry is the
 *  footprint outline (already public on the InSAR map); danger_level is the LIVE tier. */
export interface InsarCandidate {
  insar_building_id: number;
  aoi_code: string;
  distance_m: number | null;
  height_m: number | null;
  n_floors: number | null;
  danger_level: number | null;       // 0..4
  geometry: GeoJSON.Geometry | null; // footprint polygon
}

export interface ListingCandidates {
  listing_id: string;
  coverage: InsarCoverage;
  provisional: boolean;
  candidates: InsarCandidate[];
}

/** Fetch a listing's InSAR risk badge. Public-read (no token needed); the backend
 *  treats an optional token only as attribution. Never throws here — callers render
 *  the result, and a thrown fetch error is surfaced as a query error (badge hides). */
export async function getListingRisk(listingId: string): Promise<ListingRisk> {
  return fetchJson<ListingRisk>(
    `${API_BASE_URL}/insar/listing/${encodeURIComponent(listingId)}/risk`,
  );
}

/** The plausible footprints a listing's pin could be (owner/agent only). Powers the
 *  tap-to-confirm UI: each candidate carries its live tier + footprint outline. */
export async function getListingCandidates(
  token: string,
  listingId: string,
): Promise<ListingCandidates> {
  return fetchJson<ListingCandidates>(
    `${API_BASE_URL}/insar/listing/${encodeURIComponent(listingId)}/candidates`,
    { headers: authHeaders(token) },
  );
}

/** Persist the owner's building choice (must be one of the listing's candidates). Returns
 *  the now-confirmed listing's risk (monitored + live tier). Owner/agent only. */
export async function confirmListingBuilding(
  token: string,
  listingId: string,
  insarBuildingId: number,
): Promise<ListingRisk> {
  return fetchJson<ListingRisk>(
    `${API_BASE_URL}/insar/listing/${encodeURIComponent(listingId)}/confirm`,
    {
      method: 'POST',
      headers: { ...authHeaders(token), 'Content-Type': 'application/json' },
      body: JSON.stringify({ insar_building_id: insarBuildingId }),
    },
  );
}

/**
 * Batch "is this listing ground-confirmed?" — one call for a whole page of listings
 * (no N+1). Returns a listing_id → boolean map (a human/certifier has assessed the
 * building). Auth-gated; the response carries ONLY the boolean, never flag content.
 * Empty input short-circuits to {} without a request.
 */
export async function getConfirmedListings(
  token: string,
  listingIds: string[],
): Promise<Record<string, boolean>> {
  if (listingIds.length === 0) return {};
  const res = await fetchJson<{ confirmed: Record<string, boolean> }>(
    `${API_BASE_URL}/insar/listings/confirmed`,
    {
      method: 'POST',
      headers: { ...authHeaders(token), 'Content-Type': 'application/json' },
      body: JSON.stringify({ listing_ids: listingIds }),
    },
  );
  return res.confirmed;
}

/** Build the InSAR URL with the telemetry token + optional fly-to target.
 *  `returnPath` is the Weespas page the user is leaving (a SAME-ORIGIN RELATIVE path,
 *  e.g. "/properties/abc?x=1"), so InSAR can render a "← Back to Weespas" breadcrumb.
 *  We pass only the path (never an absolute URL): InSAR rebuilds the absolute link
 *  against its OWN configured Weespas origin, so a hand-crafted link can't redirect
 *  the chip off-site. Defaults to the current location when not supplied. */
export function buildInsarUrl(session: InsarSession, returnPath?: string): string {
  const params = new URLSearchParams({ wt: session.token });
  if (session.aoi_code) params.set('aoi', session.aoi_code);
  if (session.building_id != null) params.set('building', String(session.building_id));
  const ret = returnPath ?? currentReturnPath();
  if (ret) params.set('return', ret);
  const base = session.insar_url.replace(/\/$/, '');
  return `${base}/?${params.toString()}`;
}

/** The current Weespas location as a same-origin relative path for the InSAR back-chip.
 *  We STRIP the InSAR-integration params (wt/aoi/building/return) from the query before
 *  handing it over: otherwise a Weespas page that itself still carries a `?wt=…` telemetry
 *  token (or a leftover `return=`) would smuggle that token — and an ever-nesting return —
 *  into the link InSAR shows. The back-chip must point at a clean Weespas page. No hash
 *  (InSAR doesn't need it). SSR-safe (returns '' without a window). */
const INSAR_LINK_PARAMS = ['wt', 'aoi', 'building', 'return'];
function currentReturnPath(): string {
  if (typeof window === 'undefined') return '';
  const params = new URLSearchParams(window.location.search);
  for (const p of INSAR_LINK_PARAMS) params.delete(p);
  const qs = params.toString();
  return window.location.pathname + (qs ? `?${qs}` : '');
}

/**
 * Open the InSAR risk map. InSAR is free but LOGIN-REQUIRED:
 *  - Signed-in users get a telemetry-linked deep-link, opened in THE SAME TAB so the
 *    user has a single clear place to be; InSAR shows a "← Back to Weespas" chip that
 *    returns them to the page they left (we pass it as the `return` path). Usage is
 *    still metered via the ?wt token.
 *  - Anonymous users can't open the map (it would bounce them), so we navigate to the
 *    Weespas login with a resume intent; after sign-in they land back on the map.
 * `navigate` is the router's navigate fn (same-tab redirect for the anon path). Never
 * throws: a failed token mint for a signed-in user also falls back to the login resume.
 */
export async function openInsarRiskMap(
  token: string | null,
  navigate: (path: string) => void,
  listingId?: string,
): Promise<void> {
  if (!token) {
    navigate(loginThenInsarUrl(listingId));
    return;
  }
  try {
    const session = await getInsarSession(token, listingId);
    // Same-tab: the user leaves Weespas; the InSAR back-chip (return path) brings
    // them home. A full-page assign (not router navigate) — InSAR is a separate SPA.
    window.location.assign(buildInsarUrl(session));
  } catch {
    // Token mint failed (e.g. expired session) — route through login to recover.
    navigate(loginThenInsarUrl(listingId));
  }
}

/**
 * Called from the login page after a successful sign-in when the URL carried
 * `?next=insar`. Mints the deep-link with the freshly-minted token and opens InSAR.
 * Returns true if it handled the InSAR resume (so the caller can skip its default
 * post-login navigation), false otherwise.
 */
export async function resumeInsarAfterLogin(
  token: string,
  next: string | null,
  listingId?: string | null,
): Promise<boolean> {
  if (next !== 'insar') return false;
  try {
    const session = await getInsarSession(token, listingId ?? undefined);
    // We're on the login page, so the live location is a poor "back" target — send the
    // chip to the listing they came from, or home for a nav-level open. Same-tab assign.
    const returnPath = listingId ? `/properties/${encodeURIComponent(listingId)}` : '/';
    window.location.assign(buildInsarUrl(session, returnPath));
  } catch {
    /* best-effort — if the mint fails the user is still logged in and can retry */
  }
  return true;
}
