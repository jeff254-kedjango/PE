> **⚠️ HISTORICAL (2026-04-11) — see [`STATE.md`](STATE.md) for the current frontend.**
> This audit/plan predates whole feature areas now shipped (shorts/video,
> analytics dashboards, staff & customer-care pages, agents directory). Kept for
> context only.

# Weespas Frontend — Project Audit & Completion Plan

**Date:** 2026-04-11
**Target Audience:** Kenyan Gen Z & Millennials
**Inspiration:** Booking.com / Airbnb feel

---

## WHAT'S DONE (Frontend)

### Design System (90% Complete)
- **Color palette**: Midnight Emerald (`#022C22`), Soft Sand (`#F5F5F4`), Electric Lime (`#BFFF00`) — all defined in `variables.css`
- **Typography**: Plus Jakarta Sans (headings) + Nunito (body) loaded via Google Fonts
- **Spacing, shadows, border-radius, z-index**: Full token system in `variables.css`
- **Animations**: heartPop, fadeIn, slideUp, shimmer, pulse — all in `animations.css`
- **CSS reset & utilities**: Complete in `reset.css` and `utilities.css`

### Components (Built & Styled)
- **Navbar** — sticky, scroll-aware, mobile hamburger drawer with slide animation
- **Hero Section** — full viewport, background image, emerald gradient overlay, search bar, quick stats
- **Footer** — 3-column grid, social links, contact info, back-to-top
- **SearchPanel** — Sale/Rent toggle, lat/lng/radius inputs, property type, beds, baths, price range, geolocation button
- **PropertyGallery** — featured property carousel with prev/next navigation
- **PropertyList** — responsive grid with loading skeletons, error/empty states
- **PropertyCard** — image, title, distance, price (basic styling)
- **UI Components**: Badge, FavoriteButton (heart animation), ListingTypeBadge (Sale/Rent), PriceDisplay (glassmorphism), VerifiedBadge, VibeTag, SkeletonCard

### Data Layer (100% Complete)
- **API integration**: All endpoints wired (`fetchPropertyList`, `filterProperties`, `getNearby`, `getDetails`, `searchProperties`, `getFeatured`)
- **React Query**: Configured with 2min stale time, 15min GC, retry:1
- **Custom hooks**: `usePropertySearch` (infinite scroll), `useNearbySearch` (debounced geo), `usePropertyDetails`, `useFavorites` (localStorage), `useGeolocation`
- **TypeScript types**: Full type coverage for Property, Agent, Address, Images, Videos, Pagination
- **Utilities**: `formatPrice` (KES 5M, 25K/mo), `formatDate` (relative), `formatDistance` (m/km), `getVibeTags`

### Backend (100% Complete)
- FastAPI + SQLAlchemy with 6 models, 9 API endpoints
- Geo-spatial search (Haversine), advanced filtering, text search, pagination
- 100+ seeded Kenyan properties across Nairobi, Mombasa, Kisumu
- Agent management, property categories, soft deletes, view tracking

---

## Roles & Permissions

### Role Hierarchy

| Role | Level | Description |
|------|-------|-------------|
| **admin** | Highest | Full platform control. Can manage all users, properties, roles, and deletion requests. |
| **staff** | Mid | Moderation & user oversight. Can view all users/agents and request deletions (requires admin approval). |
| **agent** | Mid | Property listing agents. Can create, update, and delete their own properties and media. |
| **user** | Base | Default role on registration. Can browse properties, save favorites, and manage their profile. |

### Auth Dependencies (Backend)

Defined in `weespas/services/auth_service.py`:

| Dependency | Roles Allowed |
|------------|---------------|
| `get_current_user` | Any authenticated user |
| `require_agent` | agent, staff, admin |
| `require_staff` | staff, admin |
| `require_admin` | admin only |
| `verify_property_ownership` | Admins bypass; agents/staff must own the property (`agent_id` match) |

### Backend Endpoint Permissions

#### Public (No Auth)

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/auth/register` | POST | Register new user (always assigned role `user`) |
| `/auth/login` | POST | Login with email/password or phone/password, request OTP |
| `/auth/verify-otp` | POST | Verify 6-digit OTP, receive auth token |
| `/auth/resend-otp` | POST | Resend OTP (rate-limited: 3 per 15 min) |
| `/properties` | GET | List all active properties with pagination |
| `/properties/search/query` | GET | Full-text search on title/description |
| `/properties/nearby` | GET | Find properties within radius (Haversine) |
| `/properties/categories` | GET | List all property categories |
| `/properties/featured` | GET | Get featured properties |
| `/properties/filter` | POST | Advanced multi-parameter filtering |
| `/properties/{id}` | GET | Get property details (increments view count) |
| `/agents/public` | GET | List active agents (public profiles) |
| `/agents/public/{id}` | GET | Get single agent's public profile |
| `/agents/public/{id}/properties` | GET | List agent's active properties |
| `/contact` | POST | Submit contact form inquiry |

#### Authenticated (Any Role)

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/auth/me` | GET | Get current user's profile |

#### Agent+ (agent, staff, admin)

| Endpoint | Method | Purpose | Ownership |
|----------|--------|---------|-----------|
| `/properties` | POST | Create new property listing | Agents auto-assigned to own `agent_id`; admins can specify any |
| `/properties/{id}` | PUT | Update property fields | Must own property. Non-admins cannot change `is_featured` or `is_active` |
| `/properties/{id}` | DELETE | Soft-delete property (marks inactive) | Must own property |
| `/properties/{id}/images` | POST | Upload images (max 10 MB each, max 20) | Must own property |
| `/properties/{id}/images/{img_id}` | DELETE | Delete specific image | Must own property |
| `/properties/{id}/videos` | POST | Upload video (max 100 MB) | Must own property |
| `/properties/{id}/videos/{vid_id}` | DELETE | Delete specific video | Must own property |
| `/agents/me/properties` | GET | List own properties | Admins without `agent_id` see all properties |
| `/agents/me/stats` | GET | Dashboard stats | Admins without `agent_id` see platform-wide stats |
| `/agents/search` | GET | Search agents by name | — |
| `/agents/{id}/properties` | GET | View an agent's property listings | — |

#### Staff+ (staff, admin)

| Endpoint | Method | Purpose | Restrictions |
|----------|--------|---------|--------------|
| `/staff/users` | GET | List active users with full details | Can filter by role, search by name/email/phone |
| `/staff/users/{id}` | GET | Get user details | — |
| `/staff/agents` | GET | List all active agents with property counts | Can search by name |
| `/staff/deletion-requests` | POST | Request deletion of a user/agent | Cannot target admins. Prevents duplicate pending requests |
| `/staff/deletion-requests` | GET | List own submitted deletion requests | — |

#### Admin Only

| Endpoint | Method | Purpose | Restrictions |
|----------|--------|---------|--------------|
| `/admin/users` | GET | List all users (including inactive) | Can filter by role, search by name/email/phone |
| `/admin/users/{id}` | GET | Get full user details | — |
| `/admin/users/{id}/role` | PATCH | Assign role to any user | Cannot demote self |
| `/admin/users/{id}/status` | PATCH | Activate/deactivate user account | Cannot deactivate self |
| `/admin/users/{id}` | DELETE | Permanently delete user | Cannot delete self |
| `/admin/deletion-requests` | GET | List all deletion requests (pending/approved/rejected) | — |
| `/admin/deletion-requests/{id}` | PATCH | Approve or reject deletion request | If approved, target user is permanently deleted |
| `/admin/promote-agent/{id}` | POST | Promote user to agent role | Links user to agent profile |

### Frontend Permission Checks

#### Route Guards

| Route | Allowed Roles | Redirect | File |
|-------|---------------|----------|------|
| `/stats` (Agent Dashboard) | agent, staff, admin | → `/profile` | `StatsPage.tsx:55` |

#### Conditional UI

| Feature | Visible To | Hidden From | File |
|---------|-----------|-------------|------|
| "Agent Dashboard" link in Profile | agent, admin | user, staff | `ProfilePage.tsx:125` |
| Dashboard header role label | admin → "Admin"; others → "Agent" | — | `StatsPage.tsx:129` |
| Admin user search query | admin | user, agent, staff | `useUnifiedSearch.ts:62` |
| "Assign Permissions" button | admin | user, agent, staff | `UnifiedSearchPanel.tsx:213` |
| "Delete" user button | admin | user, agent, staff | `UnifiedSearchPanel.tsx:241` |
| "View Profile" button | All (when agent profile exists) | — | `UnifiedSearchPanel.tsx:251` |

### Permission Summary Matrix

| Action | user | agent | staff | admin |
|--------|:----:|:-----:|:-----:|:-----:|
| Register / Login | ✓ | ✓ | ✓ | ✓ |
| Browse properties & agents (public) | ✓ | ✓ | ✓ | ✓ |
| Search / filter properties | ✓ | ✓ | ✓ | ✓ |
| Save favorites | ✓ | ✓ | ✓ | ✓ |
| Create property listing | ✗ | ✓ | ✓ | ✓ |
| Update own property | ✗ | ✓ | ✓ | ✓ |
| Delete own property | ✗ | ✓ | ✓ | ✓ |
| Upload / delete media | ✗ | ✓ | ✓ | ✓ |
| View agent dashboard & stats | ✗ | ✓ | ✓ | ✓ |
| Toggle `is_featured` / `is_active` | ✗ | ✗ | ✗ | ✓ |
| View all user profiles | ✗ | ✗ | ✓ | ✓ |
| List all agents (staff view) | ✗ | ✗ | ✓ | ✓ |
| Request user deletion | ✗ | ✗ | ✓ | ✓ |
| Assign roles to users | ✗ | ✗ | ✗ | ✓ |
| Activate / deactivate users | ✗ | ✗ | ✗ | ✓ |
| Permanently delete users | ✗ | ✗ | ✗ | ✓ |
| Approve / reject deletion requests | ✗ | ✗ | ✗ | ✓ |
| Promote user to agent | ✗ | ✗ | ✗ | ✓ |

### Special Rules & Safeguards

1. **Admin self-protection** — Admins cannot demote themselves, deactivate themselves, or delete themselves
2. **Staff cannot target admins** — Staff deletion requests targeting admin users are rejected (403)
3. **Default role** — New registrations always get `role: "user"`. Promotion requires admin action
4. **Ownership enforcement** — Agents/staff must own a property (`agent_id` match) to modify it. Admins bypass ownership checks
5. **Deletion workflow** — Staff submit deletion requests with a reason → admin approves/rejects → if approved, user is permanently deleted. Duplicate pending requests are prevented (409)
6. **Admin seeding** — On startup, `kwemangenyagrowa@gmail.com` is always set to admin role (`main.py`)
7. **Property field restrictions** — `is_featured` and `is_active` fields are silently stripped from non-admin update requests

### Implementation Status

#### Implemented (Backend + Frontend)

| Feature | Backend Endpoint | Frontend Location | Gated By |
|---------|-----------------|-------------------|----------|
| Search users/agents/properties | `GET /admin/users`, `GET /staff/agents`, `GET /properties/search/query` | `UnifiedSearchPanel.tsx` + `useUnifiedSearch.ts` | admin (user query), all (agents/properties) |
| Assign roles | `PATCH /admin/users/{id}/role` | `UnifiedSearchPanel.tsx:213` — role dropdown in search results | `isAdmin` |
| Delete users | `DELETE /admin/users/{id}` | `UnifiedSearchPanel.tsx:241` — delete button with confirmation | `isAdmin` |
| View agent profile | `GET /agents/public/{id}` | `UnifiedSearchPanel.tsx:251` — navigates to `/agents/{id}` | All (when agent profile exists) |

#### Backend-Only (Endpoints Exist, No Frontend UI)

| Feature | Backend Endpoint | Priority | Notes |
|---------|-----------------|----------|-------|
| Activate/deactivate users | `PATCH /admin/users/{id}/status` | **HIGH** | Admin can toggle `is_active`. Self-protection in place |
| List deletion requests | `GET /admin/deletion-requests` | **HIGH** | Returns all pending/approved/rejected requests |
| Approve/reject deletion requests | `PATCH /admin/deletion-requests/{id}` | **HIGH** | If approved, target user is permanently deleted |
| Staff submit deletion requests | `POST /staff/deletion-requests` | **MEDIUM** | Staff submits with reason; cannot target admins |
| Staff view own deletion requests | `GET /staff/deletion-requests` | **MEDIUM** | Staff sees their submitted requests |
| Promote user to agent | `POST /agents/promote-agent/{id}` | **MEDIUM** | Located in `agents.py`, not `admin.py`. Links user to agent profile |
| Get single user details | `GET /admin/users/{id}`, `GET /staff/users/{id}` | **LOW** | Detail views — useful for dedicated user profile panel |

#### Implementation Plan

**Phase A — Admin Dashboard Enhancements (StatsPage.tsx)**

1. **User activate/deactivate toggle** — Add to `UnifiedSearchPanel.tsx` action bar. When admin clicks a person result, show an "Activate"/"Deactivate" toggle pill alongside existing buttons. Wire to `PATCH /admin/users/{id}/status`. Add `patchUserStatus()` to `src/api/admin.ts`. Self-protection: disable toggle if target is the logged-in admin.

2. **Promote to agent** — Add "Promote to Agent" pill button in the action bar for user-category results where `role === 'user'`. Wire to `POST /agents/promote-agent/{id}`. Add `promoteToAgent()` to `src/api/admin.ts`. Hide button if user is already an agent or has an `agent_id`.

3. **Deletion request management** — Build a new section in StatsPage (admin-only tab or section) that lists all deletion requests. Each request card shows: requester (staff name), target user, reason, status, date. Admin can approve (triggers permanent delete) or reject. Wire to `GET /admin/deletion-requests` and `PATCH /admin/deletion-requests/{id}`. Add `listDeletionRequests()` and `handleDeletionRequest()` to `src/api/admin.ts`.







~~4. **Staff dashboard label fix** — `StatsPage.tsx:129` currently shows "Agent" for staff. Change to show "Staff" when `user.role === 'staff'`.~~ **DONE**

~~5. **Staff deletion request submission** — Add a "Request Deletion" button to UnifiedSearchPanel action bar, visible only to staff (and admin). When clicked, opens a reason input modal, then calls `POST /staff/deletion-requests`. Staff cannot target admins (backend enforces 403). Add `submitDeletionRequest()` to `src/api/admin.ts`.~~ **DONE**

~~6. **Staff view own requests** — Add a section in StatsPage (staff+ only) showing the staff member's submitted deletion requests and their current status (pending/approved/rejected). Wire to `GET /staff/deletion-requests`.~~ **DONE**

**Phase C — Dedicated Admin Panel** ✅ **DONE**

~~7. **Admin panel route** (`/admin`) — Dedicated admin page with tabs: Users, Deletion Requests, System Stats. This separates admin functions from the agent property dashboard. Currently, admin actions live inside the search drawer which is adequate for MVP but not for full management workflows.~~ **DONE** — `AdminPage.tsx` at `/admin` with three tabs (Users, Deletion Requests, System Stats), gated to `role === 'admin'`. Reuses `UnifiedSearchPanel` for user management, `useDeletionRequests` (filterable by pending/approved/rejected) for moderation, and `useAgentStats` (admin sees platform-wide totals) for system metrics. Linked from `ProfilePage.tsx` settings menu (admin-only).

---

## WHAT'S NOT DONE

### Critical Missing Pieces

| # | Feature | Status | Impact |
|---|---------|--------|--------|
| 1 | **PropertyDetails page/modal** | Bare inline styles only — no proper layout, gallery, agent card, or contact | **HIGH** — users can't view full listings |
| 2 | **MobileBottomNav CSS** | Component exists, **zero styling** | **HIGH** — broken on mobile |
| 3 | **Map View** | Referenced everywhere (Hero, BottomNav) but **not built** | **HIGH** — core feature for proximity UX |
| 4 | **Authentication pages** | Login/Register routes in nav but **no pages exist** | **HIGH** — no user accounts |
| 5 | **Color scheme conflict** | `src/styles.css` has old red/blue palette conflicting with emerald/lime system | **MEDIUM** — visual inconsistency |

### Missing Features from UX Strategy

| # | Feature | From Strategy | Status |
|---|---------|---------------|--------|
| 6 | **"Stories" style photo carousel** | "Visual Storytelling" pillar | Not built — no image gallery/lightbox |
| 7 | **Video Tours / Reels** | Video integration recommendation | Type defined, **no UI** |
| 8 | **Map/List toggle** | "Map-First Toggle" recommendation | **Not built** |
| 9 | **Sorting UI controls** | API supports sort_by/sort_order | **No frontend controls** |
| 10 | **Text search bar** | API supports `/search/query` | **Not wired to UI** |
| 11 | **Favorites page** | Referenced in BottomNav | **Not built** |
| 12 | **User Profile page** | Referenced in BottomNav | **Not built** |
| ~~13~~ | ~~**Advanced Search modal**~~ | ~~Button exists in SearchPanel~~ | ~~**DONE** — modal with city, county, size range, parking, year built, engineer certified, featured toggle, count badge~~ |
| 14 | **Toast/notification system** | Needed for favorites, errors, actions | **Not built** |
| 15 | **"Quick View" interaction** | Hover/long-press on cards (Step 1 of strategy) | **Not built** |

---

## STEP-BY-STEP PLAN TO FINISH

### Phase 1: Fix What's Broken (1-2 days)

~~**Step 1** — Delete or merge `src/styles.css` (the old red/blue palette). Move any still-needed styles into the proper design system files. This eliminates the color conflict.~~ **DONE**

~~**Step 2** — Style `MobileBottomNav.tsx`. Create `MobileBottomNav.css` with: Fixed bottom bar, 4 equal tabs with icons + labels, Active state indicator (Electric Lime underline), Favorites count badge, Safe area inset for notched phones.~~ **DONE**

~~**Step 3** — Build a proper `PropertyDetails` page/modal: Full-width image carousel with Stories progress dots, glassmorphic price overlay, property specs grid, distance/verified/vibe badges, agent contact card with Call/WhatsApp CTAs, location section with map placeholder, share + favorite buttons, slide-up on mobile + side panel on desktop.~~ **DONE**

### Phase 2: Core Features (3-5 days)

~~**Step 4** — Add **text search** to the Hero search bar. Wire it to the existing `/search/query` endpoint. Show results in the PropertyList below.~~ **DONE**

~~**Step 5** — Add **sorting controls** above the PropertyList (dropdown: "Nearest", "Price: Low-High", "Price: High-Low", "Newest"). Wire to the existing `sort_by`/`sort_order` params.~~ **DONE**

~~**Step 6** — Build the **Map/List toggle**: Use Leaflet.js (free, no API key) or Google Maps. Map markers at each property's lat/lng. Clicking marker shows mini PropertyCard popup. Persistent toggle button visible at all times (per strategy).~~ **DONE**

~~**Step 7** — Build **image gallery/lightbox** for property details: Swipeable on mobile, arrow keys on desktop. "Stories" progress bar across the top (per strategy). Lazy-loaded thumbnails.~~ **DONE**

~~**Step 8** — Build **Favorites page** (`/favorites`): Read from `useFavorites` hook. Reuse PropertyList grid layout. Empty state: "No saved properties yet".~~ **DONE**

### Phase 3: Authentication & Profiles (3-4 days)

~~**Step 9** — Build **Login page** (`/login`): Phone number + OTP (Kenyan market preference) or email/password, Social login buttons (Google, Apple), "Sign up" link, Auth state management (Context).~~ **DONE**

~~**Step 10** — Build **Register page** (`/register`): **DONE**~~

~~Prompt:~~

~~Role: Senior Frontend Engineer (React + TypeScript, modern UX best practices)~~
~~Task: Implement a Register page and fix global Navbar behavior inconsistencies across routes in a real-estate platform (Weespas).~~

~~🔧 Part 1 — Build Register Page (/register)~~

~~Create a fully responsive Register page with the following:~~

~~Input fields:~~
~~Full Name~~
~~Phone Number (Kenyan format validation preferred)~~
~~Email~~
~~Password~~
~~Terms & Conditions checkbox (required before submission)~~
~~Submit button (disabled until form is valid)~~
~~Basic validation:~~
~~Email format~~
~~Password minimum length~~
~~Required fields~~
~~Show inline error messages~~
~~Add loading state on submit~~
~~Clean, modern UI consistent with Weespas design (minimal, Airbnb-style)~~
~~🎯 Part 2 — Fix Navbar Visibility Across Pages~~

~~Problem:~~

~~On the homepage, the navbar is transparent over a hero section → works correctly~~
~~On other pages (no hero), the navbar remains transparent → becomes invisible on white background~~
~~✅ Implement a robust Navbar system with route-aware styling:~~
~~1. Add route-based detection~~
~~Use useLocation() from React Router~~
~~Detect if current route is homepage ("/")~~
~~2. Apply conditional styling logic~~
~~On homepage ("/"):~~
~~Navbar is transparent initially~~
~~On scroll → becomes solid white background~~
~~Logo switches from white → dark~~
~~On ALL other pages:~~
~~Navbar should ALWAYS be:~~
~~Solid white background~~
~~Dark logo~~
~~No transparency at any time~~
~~3. Combine scroll + route logic cleanly~~
~~Maintain scroll state (e.g., isScrolled)~~
~~Maintain route state (isHome)~~
~~Final logic:~~
~~If isHome && !isScrolled → transparent navbar~~
~~Otherwise → solid navbar~~
~~4. Ensure proper layering~~
~~Navbar should always be visible:~~
~~Use position: fixed~~
~~Add appropriate z-index~~
~~Avoid being hidden behind content~~
~~5. Improve UX polish~~
~~Add smooth transition:~~
~~background-color (fade)~~
~~logo color swap~~
~~Ensure readability:~~
~~Text/icons contrast correctly in both states~~
~~🎨 Output Requirements~~
~~Provide:~~
~~React component code (Register page)~~
~~Updated Navbar logic (with route + scroll handling)~~
~~Minimal CSS (or Tailwind if preferred)~~
~~Code should be clean, modular, and production-ready~~
~~Avoid duplication and keep logic centralized~~
~~💡 Goal~~

~~Create a seamless, premium UX where:~~
~~Navbar always remains visible and readable across all pages~~
~~Homepage retains its modern transparent hero aesthetic~~
~~Register page feels polished and trustworthy for Kenyan users~~

~~**Step 11** — Build **User Profile page** (`/profile`): **DONE**~~
~~- Avatar, name, contact info~~
~~- Saved properties count~~
~~- Search history (optional)~~
~~- Settings/preferences~~

~~**Step 12** — Add **backend auth endpoints** (currently missing from FastAPI backend):~~ **DONE**
~~- POST `/auth/register`, POST `/auth/login`, POST `/auth/verify-otp`~~
~~- JWT token management~~
~~- Protected endpoints for favorites sync~~

### Phase 4: Polish & Gen-Z Appeal (2-3 days)

~~**Step 13** — **Add Quick View interaction**:~~ **DONE**

~~- Desktop: hover on card shows expanded preview (price, specs, distance, mini gallery)~~

~~- Mobile: long-press triggers bottom sheet preview~~

~~**Step 13a** — Style and Optimize the Main Carousel:~~ **DONE**

~~- Layout: Ensure the carousel container occupies exactly 2/3 of the available width on desktop, maintaining balanced, aesthetically pleasing padding on both sides.~~

~~- Gen-Z & Millennial UX/UI: Apply a modern, clean, and highly engaging design language. Focus on sleek micro-interactions, smooth transitions, and a clutter-free look.~~

~~- Color Scheme Constraint: Do not invent or recommend a new color scheme. Strictly apply the project's existing color palette to all UI elements.~~

~~- Media & Navigation: Implement frictionless, intuitive navigation controls (e.g., modern arrows or swipe gestures). Ensure all property images are rendered crisply without any blurriness by utilizing proper image sizing, high-res source sets, and appropriate object-fit CSS properties.~~

~~**Step 13b** — **Build and Configure the Filter Card (SearchPanel.tsx)**:~~ **DONE**

~~- Layout: Position the filter card cleanly in the remaining 1/3 space on the right side of the screen (desktop view).~~

~~- Field Requirements: Implement the following interactive fields: "Use my location to search", "For rent", "For sale", "Radius", "Property Types" (populate dynamically using the property categories from our backend API), "Beds", "Baths", "Min-price", and "Max-price".~~

~~- Compound Filtering Logic: Configure the local state and event handlers so these filter fields can operate both independently (e.g., searching only by Property Type) or collectively for highly refined searches (e.g., Location + Radius + 2 Beds + 1 Bath + For Rent + Max Price). The component should compile these into a structured query object.~~

~~- Backend API Integration: Provide the necessary adjustments or boilerplate for the backend API so it is equipped to accept, parse, and accurately filter database results using this complex, multi-parameter input.~~

~~- Mobile View Tweaks: Refactor the mobile responsive view specifically for SearchPanel.tsx so that the "Beds" and "Baths" input fields sit side-by-side on the exact same line (e.g., using a flex row container) to maximize vertical screen real estate.~~


~~**Step 14** — Add **Video Tour** placeholder in PropertyDetails:~~ **DONE**
~~- Play button overlay on thumbnail~~
~~- Video player modal (use HTML5 video, data already has `streaming_url`)~~

~~**Step 15** — **Fix Favorites page property cards showing skeleton instead of real images**:~~ **DONE**
~~- **Problem**: `PropertyList` loading skeleton markup uses `skeleton-block skeleton-image` placeholder divs. When favorites load, cards should display the actual property image from `property.main_image.thumbnail_url` (already handled in `PropertyCard.tsx`), but the loading state lingers or the image data isn't present in the fetched favorites.~~
~~- **Task A**: Audit `FavoritesPage.tsx` → ensure `useQueries` responses include full image data (check that `fetchPropertyDetails` returns `main_image` with `thumbnail_url`). If the API returns images in a nested format, map them correctly.~~
~~- **Task B**: In `PropertyCard.tsx`, verify the fallback logic — if `main_image.thumbnail_url` is missing but `images[]` array exists, use `images[0].image_url` as fallback.~~
~~- **Task C**: Ensure `PropertyList` skeleton only shows during `isLoading === true` and never when data is already available. Add a brief fade-in transition from skeleton → real card for polish.~~

~~**Step 16** — **Fix Favorites page scrolling to footer on load**:~~ **DONE**
~~- **Problem**: When navigating to `/favorites`, the page scrolls down past the content to the footer area instead of showing the top of the page.~~
~~- **Task A**: Add `window.scrollTo(0, 0)` in a `useEffect` inside `FavoritesPage.tsx` on mount.~~
~~- **Task B**: Alternatively, create a `<ScrollToTop />` wrapper component using `useLocation()` from React Router that runs `window.scrollTo(0, 0)` on every route change — place it inside `<BrowserRouter>` in `App.tsx` so ALL page navigations start at top.~~
~~- **Task C**: Ensure `.favorites-page` CSS has `min-height: 100vh` (currently `60vh`) so the content area fills the viewport even with few favorites, preventing the footer from being visible on load.~~

~~**Step 17** — **Add Realtor Agent backend role & permissions system**:~~ **DONE**
~~- **Problem**: The `User` model has no `role` field. The `Agent` model in the backend is a separate entity not linked to `User`. Property CRUD endpoints (`POST /properties`, `PUT /properties/{id}`, `DELETE /properties/{id}`) have **zero auth or permission checks** — anyone can modify any property.~~
~~- **Task A — Add `role` field to User model** (`weespas/models/user.py`)~~
~~- **Task B — Link User ↔ Agent** via `agent_id` FK on User~~
~~- **Task C — Create permission dependencies** (`require_agent`, `require_admin`, `verify_property_ownership`)~~
~~- **Task D — Protect property endpoints**: POST (agent+), PUT (agent+ own only), DELETE (admin only)~~
~~- **Task E — Agent-specific endpoints** (`/agents/me/properties`, `/agents/search`, `/agents/{id}/properties`, `/agents/me/stats`, `/admin/promote-agent`)~~
~~- **Task F — Update auth response** to include `role` and `agent_id` in JWT and login response~~

~~**Step 18** — **Add Agent/Admin backend link in Profile page (frontend)**:~~ **DONE**
~~- **Problem**: Users with `agent` or `admin` roles need a visible link to the backend management area (stats page) in their profile. Regular users must NOT see this link.~~
~~- **Task A — Update `User` type** (`src/types/auth.ts`):~~
  ~~- Add `role?: 'user' | 'agent' | 'admin'` field to the `User` interface~~
~~- **Task B — Update `AuthContext`** to persist and expose the role from the login/register response~~
~~- **Task C — Add conditional "Agent Dashboard" link in `ProfilePage.tsx`**:~~
  ~~- In the Settings section, add a new menu item **above** "Saved Properties":~~
    ~~```~~
    ~~🏠 Agent Dashboard → /stats~~
    ~~```~~
  ~~- Only render this link if `user.role === 'agent' || user.role === 'admin'`~~
  ~~- Style it with a distinct accent (e.g., Electric Lime left border or icon tint) to differentiate it from regular menu items~~
~~- **Task D — Create placeholder `/stats` route** in `App.tsx`:~~
  ~~- Add route: `<Route path="/stats" element={<StatsPage />} />`~~
  ~~- Build a minimal `StatsPage.tsx` placeholder with "Agent Dashboard — Coming Soon" message~~
  ~~- Guard the route: if user is not agent/admin, redirect to `/profile`~~

~~**Step 19** — **Add interactive Map to Favorites page**:~~ **DONE**
~~- **Problem**: Favorites page shows a flat grid of cards with no spatial context. The existing `PropertyMap.tsx` component (Leaflet.js) is fully functional on the homepage but not used on favorites.~~
~~- **Task A**: Import `PropertyMap` and `ViewToggle` into `FavoritesPage.tsx`~~
~~- **Task B**: Add a List/Map toggle above the property grid (reuse `ViewToggle` component)~~
~~- **Task C**: When map view is selected, render `<PropertyMap properties={properties} onSelect={setSelectedProperty} />` — markers for each favorited property with popups~~
~~- **Task D**: Ensure the map bounds fit only the favorited properties (not all properties)~~
~~- **Task E**: Handle edge case — if only 1 favorite, center map on it with zoom 15 instead of fitBounds~~

~~**Step 20** — **Add interactive Map to PropertyDetails modal**:~~ **DONE**
~~- **Problem**: PropertyDetails has a "Map coming soon" placeholder (`pd-location__map-placeholder` div at line 257) instead of an actual map showing the property's location.~~
~~- **Task A**: Replace the placeholder div with a small embedded Leaflet map:~~
  ~~- Use the property's `latitude` and `longitude` to center the map~~
  ~~- Single marker at the property location with a popup showing the address~~
  ~~- Fixed height container (~200px), rounded corners, non-interactive zoom (view-only or limited interaction)~~
~~- **Task B**: Only render the map if `property.latitude` and `property.longitude` are defined — keep the placeholder as fallback for properties without coordinates~~
~~- **Task C**: Use the same Leaflet instance/tiles as `PropertyMap.tsx` for visual consistency (OpenStreetMap tiles, same marker styling)~~
~~- **Task D**: Add a "Get Directions" button below the map that opens Google Maps with the property coordinates: `https://www.google.com/maps/dir/?api=1&destination={lat},{lng}`~~

~~**Step 21** — **Fix OTP authentication flow**:~~ **DONE**
~~- **Problem**: OTP login is not working. The backend generates a 6-digit OTP and stores it in the database but **never actually sends it via SMS**. The code has a comment: "In production, send OTP via SMS (e.g., Africa's Talking API)". The frontend expects the OTP to arrive on the user's phone but it never does.~~
~~- **Task A — Integrate Africa's Talking SMS API** (backend `weespas/services/sms_service.py`)~~
~~- **Task B — Call SMS service in auth flow** (`weespas/services/auth_service.py`) — rate limiting (3/15min), debug logging~~
~~- **Task C — Add resend OTP endpoint** (`weespas/routers/auth.py`) — `POST /auth/resend-otp`~~
~~- **Task D — Frontend OTP UX improvements** (`LoginPage.tsx`) — countdown timer, resend via API, dev OTP display~~
~~- **Task E — Dev/testing fallback** — debug mode logs OTP to console + returns in API response~~

~~**Step 22** — Build **toast/notification system**:~~ **DONE**
~~- "Added to favorites", "Removed from favorites"~~
~~- "Location access granted", "Search results updated"~~
~~- "OTP sent!", "Login successful"~~
~~- Slide-in from bottom, auto-dismiss after 3s~~

~~**Step 23** — Add **Advanced Search modal**:~~ **DONE**
~~- Expand all filter options: parking, year built, engineer certified, size range~~
~~- "Apply Filters" with count badge showing matching results~~
~~**Step 24** — Performance & UX polish:~~ **DONE**

~~- Add `React.lazy()` + `Suspense` for route-level code splitting~~
~~- Add error boundaries per route~~
~~- Persist filters to URL params (shareable search links)~~
~~- Add page transition animations between routes~~
~~- SEO meta tags per page~~

### Phase 5: Agent Dashboard & Stats (2-3 days)


~~**Step 25** — **Design and build the Stats/Agent Dashboard page** (`/stats`):~~ **DONE**
~~- Agent overview: total properties listed, total views across all properties, properties by category breakdown~~
~~- Property management table: list of agent's properties with title, status, views, created date, edit button~~
~~- Quick actions: "Add New Property" button, "Search Agents" link~~
~~- Charts/visualizations: views over time (simple bar chart), properties by listing type (rent vs sale pie chart)~~
~~- Mobile-responsive layout with card-based stats on mobile, table view on desktop~~
~~- Protected route — only accessible to `agent` and `admin` roles~~

**Scalability & Performance Audit (applies to Step 25–28):**


> Audit the backend and frontend of the Weespas application for scalability and performance under high traffic (millions of concurrent requests).
>

> **1. Backend (FastAPI / Database layer):
>
> * Identify all API endpoints and evaluate their time and space complexity.
> * Detect N+1 query issues and apply eager loading where appropriate (e.g., SQLAlchemy `joinedload`/`selectinload`).
> * Recommend query optimizations, indexing strategies, and caching mechanisms (Redis, in-memory caching).
> * Ensure pagination is implemented for all list endpoints.
> * Highlight any blocking operations and suggest async or background task alternatives.
> * Evaluate rate limiting, connection pooling, and database load handling.
>
> **2. Frontend (React / Inertia):**
>
> * Identify components that should use lazy loading (`React.lazy`, dynamic imports).
> * Optimize API calls (debouncing, batching, avoiding redundant requests).
> * Evaluate state management efficiency and re-render patterns.
> * Suggest code-splitting opportunities and performance improvements for large datasets (virtualization, infinite scroll).
>
> **3. System-Level:**
>
> * Recommend CDN usage, load balancing strategies, and horizontal scaling options.
> * Identify bottlenecks that would break under high traffic and propose solutions.
>
> **Provide:**
>
> * Specific code-level suggestions
> * Refactored examples where applicable
> * Clear explanation of trade-offs

### Phase 6: Production Readiness (1-2 days)

**Step 26** — Migrate backend from SQLite to PostgreSQL (config is already set up for it).

**Scalability & Performance Audit:**

> **1. Backend (FastAPI / Database layer):**
>
> * Identify all API endpoints and evaluate their time and space complexity.
> * Detect N+1 query issues and apply eager loading where appropriate (e.g., SQLAlchemy `joinedload`/`selectinload`).
> * Recommend query optimizations, indexing strategies, and caching mechanisms (Redis, in-memory caching).
> * Ensure pagination is implemented for all list endpoints.
> * Highlight any blocking operations and suggest async or background task alternatives.
> * Evaluate rate limiting, connection pooling, and database load handling.
>
> **2. Frontend (React / Inertia):**
>
> * Identify components that should use lazy loading (`React.lazy`, dynamic imports).
> * Optimize API calls (debouncing, batching, avoiding redundant requests).
> * Evaluate state management efficiency and re-render patterns.
> * Suggest code-splitting opportunities and performance improvements for large datasets (virtualization, infinite scroll).
>
> **3. System-Level:**
>
> * Recommend CDN usage, load balancing strategies, and horizontal scaling options.
> * Identify bottlenecks that would break under high traffic and propose solutions.
>
> **Provide:**
>
> * Specific code-level suggestions
> * Refactored examples where applicable
> * Clear explanation of trade-offs

**Step 27** — Add image upload to backend (currently URL-only). Use Cloudinary or AWS S3 for CDN.

**Scalability & Performance Audit:**

> **1. Backend (FastAPI / Database layer):**
>
> * Identify all API endpoints and evaluate their time and space complexity.
> * Detect N+1 query issues and apply eager loading where appropriate (e.g., SQLAlchemy `joinedload`/`selectinload`).
> * Recommend query optimizations, indexing strategies, and caching mechanisms (Redis, in-memory caching).
> * Ensure pagination is implemented for all list endpoints.
> * Highlight any blocking operations and suggest async or background task alternatives.
> * Evaluate rate limiting, connection pooling, and database load handling.
>
> **2. Frontend (React / Inertia):**
>
> * Identify components that should use lazy loading (`React.lazy`, dynamic imports).
> * Optimize API calls (debouncing, batching, avoiding redundant requests).
> * Evaluate state management efficiency and re-render patterns.
> * Suggest code-splitting opportunities and performance improvements for large datasets (virtualization, infinite scroll).
>
> **3. System-Level:**
>
> * Recommend CDN usage, load balancing strategies, and horizontal scaling options.
> * Identify bottlenecks that would break under high traffic and propose solutions.
>
> **Provide:**
>
> * Specific code-level suggestions
> * Refactored examples where applicable
> * Clear explanation of trade-offs

**Step 28** — Deploy:
- Frontend: Vercel or Netlify (Vite builds are supported natively)
- Backend: Railway, Render, or DigitalOcean (FastAPI + PostgreSQL)
- Set up CORS for production domain
- Environment variables for API URL

**Scalability & Performance Audit:**

> **1. Backend (FastAPI / Database layer):**
>
> * Identify all API endpoints and evaluate their time and space complexity.
> * Detect N+1 query issues and apply eager loading where appropriate (e.g., SQLAlchemy `joinedload`/`selectinload`).
> * Recommend query optimizations, indexing strategies, and caching mechanisms (Redis, in-memory caching).
> * Ensure pagination is implemented for all list endpoints.
> * Highlight any blocking operations and suggest async or background task alternatives.
> * Evaluate rate limiting, connection pooling, and database load handling.
>
> **2. Frontend (React / Inertia):**
>
> * Identify components that should use lazy loading (`React.lazy`, dynamic imports).
> * Optimize API calls (debouncing, batching, avoiding redundant requests).
> * Evaluate state management efficiency and re-render patterns.
> * Suggest code-splitting opportunities and performance improvements for large datasets (virtualization, infinite scroll).
>
> **3. System-Level:**
>
> * Recommend CDN usage, load balancing strategies, and horizontal scaling options.
> * Identify bottlenecks that would break under high traffic and propose solutions.
>
> **Provide:**
>
> * Specific code-level suggestions
> * Refactored examples where applicable
> * Clear explanation of trade-offs

---

## Progress Summary

| Area | Progress |
|------|----------|
| Design System (CSS tokens) | **100%** — old `styles.css` removed, emerald/lime system active |
| Component Library (UI pieces) | **90%** — missing Quick View |
| Page/View Layer | **95%** — Hero + List + Details + Map + Search + Sort + Gallery + Favorites + Login + Register + Profile + Advanced Search + Stats + Admin Panel done |
| Data Layer (API + hooks) | **100%** — all endpoints wired, search + sort connected |
| Backend Auth | **100%** — register, login, OTP, JWT, SMS delivery, agent CRUD protection done |
| Backend RBAC & Agent System | **100%** — User model has `role` + `agent_id`, property CRUD protected, ownership enforced, admin/staff/agent dependencies in place |
| OTP / SMS | **100%** — Africa's Talking integration, rate limiting (3/15min), resend endpoint, debug fallback, frontend countdown |
| Map Integration | **100%** — homepage map, Favorites map, PropertyDetails map all done (Leaflet) |
| Agent Dashboard / Stats | **100%** — `/stats` route live for agent/staff/admin with stats, charts, property table, deletion request management |
| Admin Panel | **100%** — `/admin` route with Users / Deletion Requests / System Stats tabs, gated to admin |
| **Overall Frontend** | **~90% complete** |
| **Overall Backend** | **~90% complete** |

---

## UX Strategy Reference

### The 5 Pillars (from UX Strategy doc)
1. **Hyper-Local Priority** — "Distance from you" badge on every card *(done)*
2. **"Vibe" over Specs** — Lifestyle tags like #WorkFromHomeReady *(VibeTag component built)*
3. **Thumb-First Navigation** — Bottom nav bar for mobile *(done — styled)*
4. **Visual Storytelling** — Large image cards with Stories-style progress bar *(next up — Step 7)*
5. **Map-First Toggle** — Always-visible Map/List switch *(done — Leaflet map built)*

### Brand Identity
- **Vibe:** Minimalist, sleek, "Aesthetic," and trustworthy
- **Primary:** Midnight Emerald `#022C22`
- **Background:** Soft Sand `#F5F5F4`
- **CTA:** Electric Lime `#BFFF00`
- **Fonts:** Plus Jakarta Sans (headers), Nunito (body)

### Key Recommendations Still Pending
- "Quick View" interaction (hover/long-press)
- Video Tours / Reels integration
- Glassmorphism overlay for price tags *(PriceDisplay component has this)*
- Heart icon animation for saving *(FavoriteButton has this)*
- Estate/Neighborhood in location text (e.g., "Kileleshwa, Nairobi")
- ~~**Favorites page**: real property images in cards, scroll-to-top fix, map toggle~~ **ALL DONE**
- ~~**PropertyDetails**: embedded Leaflet map replacing "Map coming soon" placeholder~~ **DONE**
- **Agent role system**: User model needs `role` field, link User ↔ Agent, protect CRUD endpoints
- **Profile backend link**: conditional "Agent Dashboard" menu item for agent/admin users
- ~~**OTP SMS delivery**: integrate Africa's Talking API, add rate limiting, resend endpoint~~ **DONE**
- **Stats/Dashboard page**: agent property management, view analytics, charts
