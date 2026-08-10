/**
 * InSAR access gate — "free, but login-required" (commercial_model.md).
 *
 * InSAR has no identity of its own; it rides Weespas's. Previously it was a fully
 * public map (anyone could open it). The commercial model now makes it free ONLY for
 * signed-in Weespas users: every visit must arrive carrying the telemetry-scoped token
 * Weespas mints on the "Risk Map" deep-link (?wt=). A direct, tokenless visit — or a
 * forged / expired token — is bounced to the Weespas login, where the user signs in and
 * re-enters via the Risk Map link.
 *
 * This gates the MAP UI only — the InSAR read API (backend/app/main.py) stays public and
 * untouched, so this is access control for the product surface, not the raw data.
 *
 * Verification is SERVER-SIDE: we don't trust the token's mere presence, we ask Weespas
 * (GET /insar/verify) to validate its signature, scope, and expiry. That blocks a
 * hand-crafted ?wt= from rendering the map. Cost is one round-trip on load; if Weespas is
 * unreachable we fail CLOSED (redirect to login) rather than expose the map.
 */

import { WEESPAS_API, getTelemetryToken, initTelemetryFromUrl } from "./telemetry";

// Where to send an unauthenticated visitor. The Weespas login page; ?next=insar lets
// Weespas bounce the user straight back into the Risk Map flow after they sign in.
// Dev default = Weespas FE on :5174. The InSAR FE owns :5173 here (backend
// insar_public_url=5173, the ?wt= deep-link target), so Weespas runs on :5174. Shares
// VITE_WEESPAS_LOGIN_URL with telemetry.ts's WEESPAS_WEB_ORIGIN so the login-bounce and
// the "Back to Weespas" breadcrumb always resolve to the same origin.
const LOGIN_URL = (
  import.meta.env.VITE_WEESPAS_LOGIN_URL || "http://localhost:5174/login"
).replace(/\/$/, "");

export type AccessState = "checking" | "granted" | "denied";

/** The login URL a denied visitor is sent to (exported for messaging / tests). */
export function loginUrl(): string {
  return `${LOGIN_URL}?next=insar`;
}

/** Redirect the browser to the Weespas login. Isolated so it can be stubbed in tests. */
export function redirectToLogin(): void {
  if (typeof window !== "undefined") {
    window.location.href = loginUrl();
  }
}

/**
 * Decide whether this visitor may see the map. Must be called AFTER the deep-link has
 * been parsed (initTelemetryFromUrl), so the ?wt token is captured. Returns "granted"
 * only when Weespas confirms the token is valid + telemetry-scoped + unexpired.
 *
 * Fails CLOSED: no token, a 401, or a network/Weespas-down error all return "denied".
 */
export async function verifyAccess(): Promise<AccessState> {
  // Ensure the token is captured from the URL even if the gate runs first.
  initTelemetryFromUrl();
  const token = getTelemetryToken();
  if (!token) return "denied"; // tokenless (direct/anonymous) visit — no map for you

  try {
    const res = await fetch(`${WEESPAS_API}/insar/verify`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    return res.ok ? "granted" : "denied";
  } catch {
    // Weespas unreachable — fail closed. A subsidence map behind a paywall-by-login
    // should never silently fall open when the gatekeeper is down.
    return "denied";
  }
}
