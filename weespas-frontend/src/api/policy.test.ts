import { describe, it, expect, vi, afterEach } from 'vitest';
import { fetchPolicyStatus } from './policy';

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

describe('fetchPolicyStatus', () => {
  it('sends the bearer token and returns the metered verdict + signals', async () => {
    const fetchMock = mockFetch(200, {
      decision: 'metered', metered: true, score: 0.74,
      signals: { volume: 120, breadth: 5, export_count: 3, automation: 0.4, corporate_domain: true },
    });
    vi.stubGlobal('fetch', fetchMock);

    const s = await fetchPolicyStatus('mytoken');
    expect(s.decision).toBe('metered');
    expect(s.metered).toBe(true);
    expect(s.signals?.breadth).toBe(5);

    const init = fetchMock.mock.calls[0][1] as RequestInit;
    expect((init.headers as Record<string, string>).Authorization).toBe('Bearer mytoken');
    expect(fetchMock.mock.calls[0][0]).toContain('/policy/me');
  });

  it('handles a free user with no profile (no signals block)', async () => {
    vi.stubGlobal('fetch', mockFetch(200, { decision: 'free', metered: false, score: 0.0 }));
    const s = await fetchPolicyStatus('tok');
    expect(s.decision).toBe('free');
    expect(s.metered).toBe(false);
    expect(s.signals).toBeUndefined();
  });
});
