import { describe, it, expect, vi, afterEach } from 'vitest';
import {
  buildInsarUrl,
  getInsarSession,
  openInsarRiskMap,
  loginThenInsarUrl,
  resumeInsarAfterLogin,
  type InsarSession,
} from './insar';

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

describe('buildInsarUrl', () => {
  it('encodes the token + deep-link target as query params', () => {
    const s: InsarSession = {
      token: 'tok-123', insar_url: 'http://localhost:5173',
      aoi_code: 'huruma', building_id: 100123,
    };
    const url = new URL(buildInsarUrl(s));
    expect(url.origin).toBe('http://localhost:5173');
    expect(url.searchParams.get('wt')).toBe('tok-123');
    expect(url.searchParams.get('aoi')).toBe('huruma');
    expect(url.searchParams.get('building')).toBe('100123');
  });

  it('omits aoi/building when the listing did not resolve (nav-level link)', () => {
    const s: InsarSession = { token: 'tok', insar_url: 'http://localhost:5173', aoi_code: null, building_id: null };
    const url = new URL(buildInsarUrl(s));
    expect(url.searchParams.get('wt')).toBe('tok');
    expect(url.searchParams.has('aoi')).toBe(false);
    expect(url.searchParams.has('building')).toBe(false);
  });

  it('does not double a trailing slash on the base url', () => {
    const s: InsarSession = { token: 't', insar_url: 'http://localhost:5173/', aoi_code: null, building_id: null };
    const url = new URL(buildInsarUrl(s, ''));
    expect(url.origin + url.pathname).toBe('http://localhost:5173/');
    expect(url.searchParams.get('wt')).toBe('t');
  });

  it('carries an explicit return path so InSAR can show a back-chip', () => {
    const s: InsarSession = { token: 't', insar_url: 'http://localhost:5173', aoi_code: null, building_id: null };
    const url = new URL(buildInsarUrl(s, '/properties/L9?x=1'));
    expect(url.searchParams.get('return')).toBe('/properties/L9?x=1');
  });

  it('defaults the return path to the current location when not supplied', () => {
    const s: InsarSession = { token: 't', insar_url: 'http://localhost:5173', aoi_code: null, building_id: null };
    // jsdom default location is "/"
    const url = new URL(buildInsarUrl(s));
    expect(url.searchParams.get('return')).toBe('/');
  });

  it('STRIPS leaked InSAR params (wt/aoi/building/return) from the default return path', () => {
    // Regression: a Weespas page whose own URL still carries a ?wt= telemetry token (or a
    // leftover return=) must NOT smuggle that token — or an ever-nesting return — into the
    // link InSAR shows. The back-chip has to point at a clean Weespas page.
    vi.stubGlobal('location', {
      ...window.location,
      pathname: '/properties/L9',
      search: '?wt=LEAKED_TOKEN&aoi=huruma&building=7&return=%2Fnested&ref=card',
    } as unknown as Location);
    const s: InsarSession = { token: 'tok', insar_url: 'http://localhost:5173', aoi_code: null, building_id: null };
    const ret = new URL(buildInsarUrl(s)).searchParams.get('return')!;
    expect(ret).toBe('/properties/L9?ref=card'); // only the non-InSAR param survives
    expect(ret).not.toContain('wt=');
    expect(ret).not.toContain('LEAKED_TOKEN');
    expect(ret).not.toContain('return=');
  });
});

describe('getInsarSession', () => {
  it('passes listing_id and returns the session', async () => {
    const fetchMock = mockFetch(200, {
      token: 'tok', insar_url: 'http://localhost:5173', aoi_code: 'kilimani', building_id: 42,
    });
    vi.stubGlobal('fetch', fetchMock);
    const s = await getInsarSession('mytoken', 'L9');
    expect(s.aoi_code).toBe('kilimani');
    expect(s.building_id).toBe(42);
    const calledUrl = (fetchMock.mock.calls[0][0] as string);
    expect(calledUrl).toContain('/insar/session-token?listing_id=L9');
  });
});

describe('loginThenInsarUrl', () => {
  it('carries the next=insar intent and optional listing', () => {
    expect(loginThenInsarUrl()).toBe('/login?next=insar');
    const u = new URL(loginThenInsarUrl('L42'), 'http://x');
    expect(u.searchParams.get('next')).toBe('insar');
    expect(u.searchParams.get('listing')).toBe('L42');
  });
});

describe('openInsarRiskMap', () => {
  it('anonymous (no token) routes to login with the resume intent — never calls the API', async () => {
    const navigate = vi.fn();
    const fetchMock = vi.fn();
    vi.stubGlobal('fetch', fetchMock);

    await openInsarRiskMap(null, navigate, 'L1');

    expect(fetchMock).not.toHaveBeenCalled();
    expect(navigate).toHaveBeenCalledWith('/login?next=insar&listing=L1');
  });

  it('authed navigates THIS tab to a deep-linked, token-bearing URL (same-tab)', async () => {
    const assign = vi.fn();
    vi.stubGlobal('location', { ...window.location, assign } as unknown as Location);
    const navigate = vi.fn();
    vi.stubGlobal('fetch', mockFetch(200, {
      token: 'tok', insar_url: 'http://localhost:5174', aoi_code: 'huruma', building_id: 7,
    }));

    await openInsarRiskMap('mytoken', navigate, 'L1');

    const openedUrl = assign.mock.calls[0][0] as string;
    expect(openedUrl).toContain('wt=tok');
    expect(openedUrl).toContain('building=7');
    expect(navigate).not.toHaveBeenCalled();
  });

  it('routes to login if minting fails (never throws)', async () => {
    const navigate = vi.fn();
    vi.stubGlobal('fetch', mockFetch(500, { detail: 'boom' }));

    await openInsarRiskMap('mytoken', navigate);

    expect(navigate).toHaveBeenCalledWith('/login?next=insar');
  });
});

describe('resumeInsarAfterLogin', () => {
  it('does nothing when next is not "insar"', async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal('fetch', fetchMock);
    const handled = await resumeInsarAfterLogin('tok', null);
    expect(handled).toBe(false);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('navigates THIS tab to InSAR deep-linked when next is "insar", with a sane return path', async () => {
    const assign = vi.fn();
    vi.stubGlobal('location', { ...window.location, assign } as unknown as Location);
    vi.stubGlobal('fetch', mockFetch(200, {
      token: 'fresh', insar_url: 'http://localhost:5174', aoi_code: 'kilimani', building_id: 9,
    }));

    const handled = await resumeInsarAfterLogin('fresh', 'insar', 'L7');

    expect(handled).toBe(true);
    const openedUrl = new URL(assign.mock.calls[0][0] as string);
    expect(openedUrl.searchParams.get('wt')).toBe('fresh');
    expect(openedUrl.searchParams.get('building')).toBe('9');
    // We're on /login at this point, so the back-chip targets the listing, not /login.
    expect(openedUrl.searchParams.get('return')).toBe('/properties/L7');
  });
});
