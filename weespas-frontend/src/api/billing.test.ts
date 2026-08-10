import { describe, it, expect, vi, afterEach } from 'vitest';
import { revealListing, fetchTiers, fetchEntitlement } from './billing';

function mockFetch(status: number, body: unknown) {
  return vi.fn().mockResolvedValue({
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
    text: async () => JSON.stringify(body),
    statusText: String(status),
  } as Response);
}

afterEach(() => { vi.restoreAllMocks(); });

describe('revealListing', () => {
  it('200 → revealed with exact coords', async () => {
    vi.stubGlobal('fetch', mockFetch(200, {
      listing_id: 'L1', latitude: -1.3, longitude: 36.8,
      street_address: 'Some St', directions_url: 'https://maps/dir', remaining: 2, newly_charged: true,
    }));
    const r = await revealListing('tok', 'L1');
    expect(r.kind).toBe('revealed');
    if (r.kind === 'revealed') {
      expect(r.latitude).toBe(-1.3);
      expect(r.directions_url).toBe('https://maps/dir');
      expect(r.newly_charged).toBe(true);
    }
  });

  it('402 → payment_required (does NOT throw)', async () => {
    vi.stubGlobal('fetch', mockFetch(402, {
      reason: 'no_window',
      tiers: [{ code: 'T1', price_kes: 20, locations: 3, window_seconds: 7200 }],
    }));
    const r = await revealListing('tok', 'L1');
    expect(r.kind).toBe('payment_required');
    if (r.kind === 'payment_required') {
      expect(r.reason).toBe('no_window');
      expect(r.tiers).toHaveLength(1);
      expect(r.tiers[0].code).toBe('T1');
    }
  });

  it('other non-2xx throws', async () => {
    vi.stubGlobal('fetch', mockFetch(500, { detail: 'boom' }));
    await expect(revealListing('tok', 'L1')).rejects.toThrow(/Reveal failed: 500/);
  });
});

describe('fetchTiers', () => {
  it('returns the tier array', async () => {
    vi.stubGlobal('fetch', mockFetch(200, {
      tiers: [{ code: 'T2', price_kes: 50, locations: 6, window_seconds: 14400 }],
    }));
    const tiers = await fetchTiers();
    expect(tiers).toHaveLength(1);
    expect(tiers[0].price_kes).toBe(50);
  });
});

describe('fetchEntitlement', () => {
  it('falls back to inactive on non-OK', async () => {
    vi.stubGlobal('fetch', mockFetch(401, {}));
    const e = await fetchEntitlement('tok');
    expect(e.active).toBe(false);
  });

  it('passes through an active window', async () => {
    vi.stubGlobal('fetch', mockFetch(200, { active: true, tier: 'T1', remaining: 3 }));
    const e = await fetchEntitlement('tok');
    expect(e.active).toBe(true);
    expect(e.remaining).toBe(3);
  });
});
