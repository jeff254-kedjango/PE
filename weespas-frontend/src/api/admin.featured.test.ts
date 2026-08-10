import { describe, it, expect, vi, afterEach } from 'vitest';
import { listFeaturedProperties, setPropertyFeatured } from './admin';

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

describe('listFeaturedProperties', () => {
  it('GETs /admin/featured with auth + pagination', async () => {
    const f = mockFetch(200, { total: 1, skip: 0, limit: 50, items: [{ id: 'P1', title: 'T' }] });
    vi.stubGlobal('fetch', f);
    const res = await listFeaturedProperties('tok', { limit: 50 });
    expect(res.items[0].id).toBe('P1');
    const [url, opts] = f.mock.calls[0];
    expect(String(url)).toContain('/admin/featured?');
    expect(String(url)).toContain('limit=50');
    expect((opts as RequestInit).headers).toMatchObject({ Authorization: 'Bearer tok' });
    // GET → no method set (fetchJson default) or explicitly GET
    expect((opts as RequestInit).method ?? 'GET').toBe('GET');
  });
});

describe('setPropertyFeatured', () => {
  it('POSTs /admin/properties/{id}/feature with the payload + auth', async () => {
    const f = mockFetch(200, { id: 'P1', is_featured: true, featured_expires_at: '2026-07-01T00:00:00Z' });
    vi.stubGlobal('fetch', f);
    const res = await setPropertyFeatured('tok', 'P1', { is_featured: true, duration_days: 7 });
    expect(res.is_featured).toBe(true);
    const [url, opts] = f.mock.calls[0];
    expect(String(url)).toContain('/admin/properties/P1/feature');
    const init = opts as RequestInit;
    expect(init.method).toBe('POST');
    expect(init.headers).toMatchObject({ Authorization: 'Bearer tok', 'Content-Type': 'application/json' });
    expect(JSON.parse(init.body as string)).toEqual({ is_featured: true, duration_days: 7 });
  });

  it('unfeature sends only is_featured:false', async () => {
    const f = mockFetch(200, { id: 'P1', is_featured: false, featured_expires_at: null });
    vi.stubGlobal('fetch', f);
    await setPropertyFeatured('tok', 'P1', { is_featured: false });
    const init = f.mock.calls[0][1] as RequestInit;
    expect(JSON.parse(init.body as string)).toEqual({ is_featured: false });
  });
});
