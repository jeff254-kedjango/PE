// §8 company-detection policy client (commercial_model.md §7, billing_architecture.md §8.2).
//
// Reads the signed-in user's OWN commercial-use verdict. `decision === 'metered'` means
// their behaviour (InSAR building views / exports / breadth / corporate domain) crossed
// the professional-scale threshold → the app shows a SOFT upsell to a business plan,
// never a block or an accusation. Anonymous users never reach here (the hook is gated on
// a token); the backend returns 'free' for them anyway.
//
// Mirrors src/api/insar.ts: fetchJson + Bearer header.
import { fetchJson, API_BASE_URL } from './config';

const authHeaders = (token: string) => ({ Authorization: `Bearer ${token}` });

export type PolicyDecision = 'free' | 'metered' | 'blocked';

export interface PolicySignals {
  volume: number;          // commercial actions (reveals + InSAR views/exports) in window
  breadth: number;         // distinct AOIs swept
  export_count: number;    // CSV/report exports
  automation: number;      // [0,1] regularity proxy
  corporate_domain: boolean;
}

export interface PolicyStatus {
  decision: PolicyDecision;
  metered: boolean;
  score: number;           // [0,1] commercial-likelihood
  signals?: PolicySignals; // absent until the user has a usage profile
}

export async function fetchPolicyStatus(token: string): Promise<PolicyStatus> {
  return fetchJson<PolicyStatus>(`${API_BASE_URL}/policy/me`, {
    headers: authHeaders(token),
  });
}
