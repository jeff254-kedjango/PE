# Weespas Frontend — Current State (authoritative)

_Last reconciled: 2026-06-22._

Single source of truth for the frontend as it exists today. `PROJECT_AUDIT.md`
(dated 2026-04-11) and `Audit_Report.md` (2026-05-04) are **historical** — they
predate whole feature areas now in `src/` and are kept for context only.

## What it is
A React 18 + TypeScript + Vite single-page app (~17.5k LOC) consuming the
Weespas backend API. Data via TanStack React Query; routing via React Router;
maps via Leaflet; charts via Recharts. Native `fetch` (no axios).

## Routes (`src/App.tsx`) — lazy-loaded with Suspense + per-route error boundary
`/` (home: Hero + search + property list/gallery + shorts) · `/favorites` ·
`/login` · `/register` · `/profile` · `/stats` (agent/staff/admin dashboard) ·
`/admin` (admin panel) · `/staff` · `/customer-care` · `/agents` ·
`/agents/:agentId`.

## Feature areas (`src/components/`)
- **layout/** — Navbar (route-aware), Hero, Footer, SearchPanel, MobileBottomNav,
  MegaMenu, Splash, ScrollToTop, PropertyGallery.
- **property/** — PropertyDetails (+ById), gallery/lightbox.
- **shorts/** — vertical video feed / Reels (ShortsShelf, VerticalVideoFeed,
  ShortItem, ShortCard).
- **analytics/** — agent/admin dashboards (engagement, category-interest,
  price-range, conversion-funnel, heatmap, summary strip).
- **map/** — PropertyMap + PropertyLocationMap (Leaflet).
- **ui/** — badges, modals (Add/Edit property, advanced search, confirm-delete),
  pagination, sort/view toggles, unified search panel, image gallery, etc.

## Auth model
- JWT stored in `localStorage` (`weespas_token`; profile cached as
  `weespas_user`) — see `src/context/AuthContext.tsx`.
- Bearer token attached per-call via `authHeaders()` (`src/api/auth.ts`);
  shared `fetchJson()` in `src/api/config.ts` sends `credentials: 'include'`
  (for the analytics session cookie) and redirects to `/login` on 401.

## Configuration
- `VITE_API_BASE_URL` (in `.env`, git-ignored; template in `.env.example`) —
  base URL of the backend API; consumed in `src/api/config.ts`. No secrets in
  the frontend env.

## Run / build / test
```bash
cd weespas-frontend
npm install
cp .env.example .env        # adjust VITE_API_BASE_URL if needed
npm run dev                 # http://localhost:5173
npm run build               # tsc + vite production build
npm run test:run            # vitest (jsdom + React Testing Library)
```

## Tests
Starter Vitest suite (see `vite.config.ts` `test` block + `src/setupTests.ts`):
- `src/utils/format.test.ts` — price/distance/bed-bath formatters.
- `src/utils/roles.test.ts` — role helpers.
- `src/components/ui/ListingTypeBadge.test.tsx` — RTL render smoke test.

Starter-level by design — extend with hook tests (React Query) and
page-level interaction tests next.

## Hygiene note
A `.gitignore` now excludes `node_modules/`, `dist/`, and `.env` (none were
ignored before). The on-disk `dist/` is a build artifact and is not tracked.
