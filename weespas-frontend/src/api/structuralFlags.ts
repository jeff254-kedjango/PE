// Structural-flag API client — the engineer/authority "second sensor" entry seam.
//
// A professional engineer or authority records a structural judgement for an InSAR
// building (CLEARED / UNSAFE / AUTH_UNSAFE); the InSAR build fuses it into the
// collapse score. Backend gates with `require_certifier`; AUTH_UNSAFE + source
// 'authority' additionally require the authority role (enforced server-side — the UI
// only mirrors it to avoid a doomed request).
//
// Mirrors the idiom in src/api/roleApplications.ts: `fetchJson` + Bearer header.
import { fetchJson, API_BASE_URL } from './config';

const authHeaders = (token: string) => ({ Authorization: `Bearer ${token}` });

// Mirrors models/insar_link.py FLAG_* (and InSAR STRUCT_*). NONE is not recordable.
export const FLAG_CLEARED = 1;
export const FLAG_UNSAFE = 2;
export const FLAG_AUTH_UNSAFE = 3;
export type FlagState = typeof FLAG_CLEARED | typeof FLAG_UNSAFE | typeof FLAG_AUTH_UNSAFE;

export type FlagSource = 'engineer' | 'authority';

export interface FlagCreate {
  aoi_code: string;
  insar_building_id: number;
  state: FlagState;
  source: FlagSource;
  observed_at?: string | null;   // ISO date (YYYY-MM-DD)
  note?: string | null;
}

export interface FlagOut {
  id: string;
  aoi_code: string;
  insar_building_id: number;
  state: number;
  source: string;
  observed_at: string | null;
  note: string | null;
  granted_by: string | null;
}

/** Record a structural flag. Throws on a 4xx (e.g. 403 if a non-authority tries
 *  AUTH_UNSAFE) — the caller surfaces the message via a toast. */
export async function createStructuralFlag(token: string, body: FlagCreate): Promise<FlagOut> {
  return fetchJson<FlagOut>(`${API_BASE_URL}/structural-flags`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...authHeaders(token) },
    body: JSON.stringify(body),
  });
}

/** The most-recent flag for a building (what the InSAR build would fuse), or null. */
export async function getLatestFlag(
  token: string, aoiCode: string, buildingId: number,
): Promise<FlagOut | null> {
  return fetchJson<FlagOut | null>(
    `${API_BASE_URL}/structural-flags/${encodeURIComponent(aoiCode)}/${buildingId}`,
    { headers: authHeaders(token) },
  );
}
