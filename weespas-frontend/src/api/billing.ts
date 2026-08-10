// src/api/billing.ts
//
// Client for the listing-location *reveal* flow + M-Pesa checkout
// (PE/billing_architecture.md §4/§6). Deliberately does NOT use the shared
// `fetchJson` helper for the reveal call: `fetchJson` throws on every non-2xx and
// hard-redirects on 401, but a 402 here is a NORMAL signal ("needs payment → open
// the chooser"), not an error. So reveal returns a discriminated union instead.
import { API_BASE_URL } from './config';

const authHeaders = (token: string): Record<string, string> => ({
  Authorization: `Bearer ${token}`,
});

// ---- tiers ---------------------------------------------------------------
export interface Tier {
  code: string;          // T1 / T2 / T3
  price_kes: number;
  locations: number;     // reveals the window buys
  window_seconds: number;
}

export interface TiersResponse {
  tiers: Tier[];
}

export async function fetchTiers(): Promise<Tier[]> {
  const res = await fetch(`${API_BASE_URL}/billing/tiers`);
  if (!res.ok) throw new Error(`Failed to load tiers: ${res.status}`);
  const body = (await res.json()) as TiersResponse;
  return body.tiers;
}

// ---- reveal --------------------------------------------------------------
export interface RevealSuccess {
  kind: 'revealed';
  listing_id: string;
  latitude: number;
  longitude: number;
  street_address: string | null;
  directions_url: string;
  remaining: number | null;
  newly_charged: boolean;
}

export interface RevealPaymentRequired {
  kind: 'payment_required';
  reason: 'no_window' | 'quota';
  tiers: Tier[];
}

export type RevealResult = RevealSuccess | RevealPaymentRequired;

/**
 * Reveal one listing's exact location. 200 → revealed; 402 → payment_required
 * (caller opens the chooser). 401 throws (caller prompts login); other non-2xx
 * throw. The exact coords are ONLY ever returned by this endpoint.
 */
export async function revealListing(token: string, listingId: string): Promise<RevealResult> {
  const res = await fetch(`${API_BASE_URL}/reveal/${listingId}`, {
    method: 'POST',
    headers: authHeaders(token),
    credentials: 'include',
  });

  if (res.status === 402) {
    const body = await res.json().catch(() => ({ reason: 'no_window', tiers: [] }));
    return { kind: 'payment_required', reason: body.reason ?? 'no_window', tiers: body.tiers ?? [] };
  }
  if (!res.ok) {
    const txt = await res.text().catch(() => res.statusText);
    throw new Error(`Reveal failed: ${res.status} ${txt}`);
  }
  const body = await res.json();
  return { kind: 'revealed', ...body };
}

// ---- entitlement snapshot ------------------------------------------------
export interface EntitlementStatus {
  active: boolean;
  tier?: string;
  quota?: number;
  used?: number;
  remaining?: number;
  expires_in_seconds?: number;
}

export async function fetchEntitlement(token: string): Promise<EntitlementStatus> {
  const res = await fetch(`${API_BASE_URL}/reveal/entitlement/me`, {
    headers: authHeaders(token),
    credentials: 'include',
  });
  if (!res.ok) return { active: false };
  return (await res.json()) as EntitlementStatus;
}

// ---- checkout (STK Push) -------------------------------------------------
export interface CheckoutResponse {
  checkout_id: string;
  status: string;       // 'pending' initially
}

export async function startCheckout(token: string, tier: string): Promise<CheckoutResponse> {
  const res = await fetch(`${API_BASE_URL}/billing/checkout`, {
    method: 'POST',
    headers: { ...authHeaders(token), 'Content-Type': 'application/json' },
    credentials: 'include',
    body: JSON.stringify({ tier }),
  });
  if (!res.ok) {
    const txt = await res.text().catch(() => res.statusText);
    throw new Error(`Checkout failed: ${res.status} ${txt}`);
  }
  return (await res.json()) as CheckoutResponse;
}

export interface CheckoutStatus {
  checkout_id: string;
  status: 'pending' | 'paid' | 'failed' | 'expired' | string;
  tier?: string;
}

export async function pollCheckout(token: string, checkoutId: string): Promise<CheckoutStatus> {
  const res = await fetch(`${API_BASE_URL}/billing/checkout/${checkoutId}`, {
    headers: authHeaders(token),
    credentials: 'include',
  });
  if (!res.ok) throw new Error(`Poll failed: ${res.status}`);
  return (await res.json()) as CheckoutStatus;
}
