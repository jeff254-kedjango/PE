# Profile Architecture — Implementation Plan

Plan for wiring up the **Edit Profile** and **Preferences** sections on
`/profile` (currently stubbed with `disabled` + "Soon" badges in
`weespas-frontend/src/pages/ProfilePage.tsx`).

Ordering reflects **value × cheapness given what's already in the
codebase**. Items the recommendation deprioritized are listed at the
bottom under [Deprioritized](#deprioritized---revisit-later).

Performance is the project's competitive edge. Every phase below notes
the cache/query strategy so the new endpoints don't regress the
existing latency budget.

---

## Implementation status (last updated 2026-05-21)

- ✅ **Phase 0** — Foundations. `PATCH /api/v1/auth/me` in `routers/auth.py`,
  `UserUpdateRequest` in `schemas/auth.py`, `useMe`/`useUpdateMe` in
  `src/hooks/useMe.ts`.
- ✅ **Phase 1** — Privacy toggle in `PreferencesPanel.tsx`.
- ✅ **Phase 2** — Edit name + avatar. `EditProfilePanel.tsx`,
  `POST /api/v1/me/avatar` reusing the property image-processing pipeline.
- ✅ **Phase 3** — Saved searches. `models/saved_search.py`,
  `routers/saved_searches.py`, `useSavedSearches` hook, list UI in
  `PreferencesPanel`, "Save search" entry point in `SaveSearchButton.tsx`
  on the home preview-controls strip.
- ✅ **Phase 4** — Hidden listings management inside `PreferencesPanel`.
- ✅ **Phase 5** — Active sessions + sign-out-everywhere.
- ✅ **Phase 6** — Notification preference columns + toggles. SMS-on-
  inquiry dispatcher itself isn't wired yet (`routers/contact.py` only
  persists the row); the preference is stored ahead of that worker.
- ✅ **Phase 7** — Change password (`POST /api/v1/auth/change-password`,
  revokes other sessions) + deletion request modal.
- ✅ **Phase 8** — Search defaults: `default_radius_km`,
  `preferred_listing_type`, `language` on the user row, applied in
  `useFilterParams` from the localStorage user mirror on first mount
  when the URL has no filters.
- ✅ **Phase 9** — Phone/email change with hashed OTP confirmation
  (`/me/phone/start-change`, `/me/phone/confirm`, same for email). OTP
  hashed at rest with HMAC-SHA256; constant-time compare on confirm.

Remaining cross-cutting work: pytest happy-path + auth-failure tests per
new endpoint; SMS-on-inquiry dispatcher (separate from the toggle); SMTP
channel for email-change OTP and `notify_inquiries_email`.

---

## Phase 0 — Foundations (one PR, unblocks the rest) ✅ COMPLETED

Everything else depends on a single new endpoint + cache key.

### Backend
- **`PATCH /api/v1/auth/me`** in `routers/auth.py`. Body is a partial
  `UserUpdateRequest` (Pydantic, all fields `Optional`). Returns the
  updated `UserResponse`. Reuses `require_user` dependency.
- **`UserUpdateRequest`** in `schemas/auth.py` — start with the fields
  Phase 1 needs (`name`, `avatar`, `is_public_profile`); grow per phase.
- **Cache invalidation**: after the UPDATE, bust any
  `analytics:agent_rank` / agent-directory Redis keys that embed the
  user's display name. Use the existing `analytics_cache._store` /
  invalidation pattern from `services/analytics_tasks.py`.

### Frontend
- **`useMe()` hook** in `src/hooks/useMe.ts` — single source of truth
  for the authed user. Wraps `GET /auth/me` with React Query
  `['auth', 'me']`, `staleTime: 5 min`, `placeholderData: prev`.
  Today `AuthContext` calls `/auth/me` directly and stores in
  `localStorage`; keep the localStorage cache for first paint, but
  every screen that needs fresh user data goes through `useMe()`.
- **`updateMe()` API helper** in `src/api/auth.ts` — `PATCH` wrapper
  that calls `queryClient.setQueryData(['auth', 'me'], next)` on
  success so every consumer re-renders without a network round-trip.

**Acceptance**: `PATCH /auth/me { name: "X" }` updates the DB, the
ProfilePage header shows "X" immediately on save, no re-fetch round-trip.

---

## Phase 1 — Privacy: `is_public_profile` toggle (≈30 min) ✅ COMPLETED

The column already exists (`models/user.py:34`), is returned by
`/auth/me` (`services/auth_service.py:62`), and has zero UI.

### Backend
- Already done. Just confirm `PATCH /auth/me` accepts `is_public_profile`.

### Frontend
- New `<PreferencesPanel>` component in
  `src/components/profile/PreferencesPanel.tsx`.
- One toggle: "Show my phone & email on my public profile."
- Optimistic update via React Query `onMutate` → roll back on error.

**Acceptance**: toggle persists across reload; backend column flips.

---

## Phase 2 — Edit name + avatar (≈½ day)

`users.name` and `users.avatar` already exist (`models/user.py:22,26`).
The image upload pipeline already exists for properties — reuse it.

### Backend
- **`POST /api/v1/me/avatar`** — multipart upload, returns
  `{ url, thumbnail_url }`. Reuse `services/image_processing.py` (the
  same Celery task that powers `process_property_image`) so we get the
  WebP transcode + multiple resolutions for free.
- **Cleanup**: when a user uploads a new avatar, mark the old object
  for async deletion via the existing media-cleanup Celery queue.
- `PATCH /auth/me` accepts `name` (max 255), `avatar` (URL).

### Frontend
- New `<EditProfilePanel>` in `src/components/profile/EditProfilePanel.tsx`.
- `<AvatarUploader>` — drag/drop or click, shows the existing image,
  uploads to `/me/avatar`, then PATCHes the returned URL onto `/auth/me`.
- Inline name editor with character counter + validation.

**Perf note**: the avatar route returns the thumbnail URL up front;
PropertyDetails-style lazy loading applies. No new image library.

**Acceptance**: avatar replaces across nav bar, profile page,
directory cards within one render cycle.

---

## Phase 3 — Saved searches (≈1 day; high retention payoff)

The filter shape already exists (`src/hooks/useFilterParams.ts`).
Saved searches are a thin server-side mirror of that state.

### Backend
- New table `saved_searches`:
  - `id` UUID PK
  - `user_id` FK → `users.id` ON DELETE CASCADE, indexed
  - `name` String(80)
  - `filters` JSONB — the exact shape `useFilterParams` produces
  - `created_at`, `last_used_at`
  - Unique on `(user_id, name)` so users can rename in place
- Endpoints, all gated by `require_user`:
  - `POST /api/v1/me/saved-searches`
  - `GET  /api/v1/me/saved-searches`
  - `DELETE /api/v1/me/saved-searches/{id}`
  - `PATCH  /api/v1/me/saved-searches/{id}` (rename, or "touch"
    `last_used_at` when applied)
- **No Redis cache**: list endpoint returns ≤25 rows per user, served
  by the indexed `(user_id, last_used_at DESC)` scan in <2ms.

### Frontend
- New section in `<PreferencesPanel>` listing saved searches.
- "Save this search" button on the home filter bar — opens a small
  name prompt, POSTs current `useFilterParams` state.
- Applying a saved search sets the URL query params via the existing
  `useFilterParams` setter — no special render path.

**Acceptance**: user can name a filter set, see it in the profile,
apply it from a chip, and have it stay after reload.

---

## Phase 4 — Hidden listings management (≈2 hours)

Data is already in the `property_dismissals` table; the screen is
missing.

### Backend
- `GET /api/v1/me/dismissals` — returns the dismissed property IDs
  joined to a minimal `PropertyListResponse` shape (one query with
  `joinedload`). Same shape `useDismissals` consumes already.
- `DELETE /api/v1/me/dismissals` (no id) — unhide all.

### Frontend
- New `<HiddenListingsSection>` inside `<PreferencesPanel>`.
- Renders a `<RelatedProperties>`-style row (reuse the component);
  each card has an "Unhide" button that calls the existing
  `removeDismissal` in `api/dismissals.ts`.
- React Query invalidation: invalidate `['dismissals']` and
  `['properties', ...]` on unhide so the homepage re-shows the listing.

**Acceptance**: user can see and unhide previously dismissed
properties; effect is immediate.

---

## Phase 5 — Active sessions + "Sign out everywhere" (≈3 hours)

Now that the session-cookie fix is in (`weespas_session` persists
across requests), `user_sessions` rows are meaningful per device.

### Backend
- `GET /api/v1/me/sessions` — paginated list (10 rows max), most
  recent first, joined to nothing. Columns shown: `geo_city`,
  `geo_county`, `user_agent` (parsed client-side), `last_seen_at`,
  `id`, plus a `is_current` boolean computed against the current
  request's `session_id`. Single indexed query.
- `DELETE /api/v1/me/sessions/{id}` — drops the row (browser cookie
  becomes orphaned → next request mints a fresh anon row).
- `DELETE /api/v1/me/sessions` — bulk delete all except the current one.
- **No Redis caching** — list is small, per-user, and freshness matters
  (security feature).

### Frontend
- New `<ActiveSessionsSection>` inside `<PreferencesPanel>`.
- Each row: device summary (UA-parsed), city, "Active now" / "X min ago",
  "Sign out" button. Confirm dialog on bulk sign-out-everywhere.
- Use a tiny UA-parsing helper (≤2 KB gzipped — avoid `ua-parser-js`,
  write a 30-line regex for the four common engines).

**Acceptance**: user sees one row per device they've used, can
revoke them individually.

---

## Phase 6 — Notification preferences (≈½ day)

### Backend
- Migration adds four boolean columns to `users`:
  `notify_inquiries_sms`, `notify_inquiries_email`,
  `notify_digest_email`, `notify_push`. All default `true` for SMS
  inquiries (current behavior), `false` for the rest.
- Africa's Talking is already wired in `services/auth_tasks.py`. In
  the contact-form handler (`routers/contact.py`), short-circuit the
  SMS dispatch when the recipient agent has `notify_inquiries_sms=false`.
- `PATCH /auth/me` accepts the four new fields.

### Frontend
- New `<NotificationsSection>` inside `<PreferencesPanel>`.
- Four toggles. Email digest + push show a "Coming soon" subtitle
  (preference still persists, ready for the future worker).

**Acceptance**: agent disables SMS-on-inquiry; submitting a contact
form against that agent does not enqueue an SMS task (verify in Celery
log).

---

## Phase 7 — Change password + Delete account (≈½ day)

### Backend
- `POST /api/v1/auth/change-password` — body: `{ old, new }`. Verifies
  `old` against `hashed_password`, applies bcrypt, invalidates all
  sessions for this user (calls the same logic as Phase 5's bulk
  delete except keeps the current cookie).
- `POST /api/v1/me/deletion-request` — wraps the existing staff
  `DeletionRequest` flow with `requested_by_id = self`. The staff
  pipeline already handles review.

### Frontend
- Modal forms in `<EditProfilePanel>` for password, in
  `<DangerZoneSection>` (new) for account deletion.
- Password modal: client-side strength meter (existing util in
  `src/utils/` if present, else inline 20-line check).
- Account deletion: two-step confirm (type the email to confirm).

**Acceptance**: password change immediately requires re-login on
other devices; deletion shows up in the existing staff queue at
`/staff` → "Deletion Requests".

---

## Phase 8 — Default search radius / preferred types / language (≈½ day)

Cheap personalization that compounds across every search.

### Backend
- Migration adds:
  - `default_radius_km` Integer, default 10
  - `preferred_listing_type` Enum(`rent`,`sale`,`null`), default null
  - `preferred_categories` String[] (postgres array), default `{}`
  - `language` Enum(`en`,`sw`), default `en`
- `PATCH /auth/me` accepts all of them.

### Frontend
- Move existing `useFilterParams` to read these as defaults on
  first mount (only when the URL has no explicit filters).
- New `<SearchDefaultsSection>` in `<PreferencesPanel>`.
- Language pref drives the i18n bundle (stub for now if i18n isn't wired).

**Perf note**: defaults are applied client-side from `useMe()` cache —
zero extra network calls per search.

**Acceptance**: setting "default radius = 25 km" changes the home
page's initial nearby radius after reload.

---

## Phase 9 — Phone / email change with OTP confirmation (≈1 day)

Most security-sensitive — do last so the rest is shipped and stable.

### Backend
- `POST /api/v1/me/phone/start-change { new_phone }` — sends OTP to
  the NEW number via Africa's Talking. Stores `pending_phone` +
  `pending_phone_otp_hash` + `pending_phone_expires_at` on the user
  row (or a small `pending_contact_changes` table — preferred so we
  don't keep mutating the users row).
- `POST /api/v1/me/phone/confirm { otp }` — verifies; promotes
  `pending_phone` → `phone`; bust the old number's lookups.
- Same pair for email (`POST /api/v1/me/email/start-change` etc.) —
  email needs SMTP, which isn't wired yet; stub the SMTP call behind
  a feature flag mirroring `celery_send_otp_enabled`.
- OTP storage: hash it at rest (the audit flags plaintext OTP as a
  P1 issue — fix it as part of this phase).

### Frontend
- Modal flow inside `<EditProfilePanel>`: "Change phone" → enter new
  number → OTP screen → success. Reuses the existing OTP component
  from `LoginPage`.

**Acceptance**: phone change requires OTP on the NEW device; failed
OTPs don't burn the existing phone.

---

## Cross-cutting

- **Migrations**: each phase that touches schema adds one migration
  script in `weespas/` (matching the existing `add_*.py` pattern).
  Run idempotently.
- **Tests**: each new endpoint gets one happy-path + one auth-failure
  pytest. No new test framework.
- **Telemetry**: each new endpoint emits a single structured log line
  (`logger.info("user.profile.updated", user_id=..., fields=[...])`)
  so the staff dashboard can later surface "Profile changes per day."
- **Frontend bundle**: the new sections live behind
  `lazy(() => import(...))` from `ProfilePage.tsx` so they don't bloat
  the initial profile-page paint. Each panel is ≤8 KB gzipped target.
- **Cache discipline**: every new GET is keyed under React Query, with
  `staleTime` set high enough that navigating away and back is a cache
  hit. No new Redis keys are needed except the existing
  agent-directory invalidation in Phase 0.

---

## Deprioritized — revisit later

These are explicitly **out of scope** for the plan above. Cost-without-
clear-business-signal at the current stage. Revisit when the data says
users want them.

- **Dark mode** — not mentioned in any audit doc. Pure design work, no
  product signal. Consider after a survey or theme-related support
  ticket trend.
- **Public profile URL / sharing** — only meaningful for agents, and
  the agent profile page (`/agents/:id`) already serves as the public
  surface.
- **Push notifications (browser Web Push)** — needs a service worker
  + VAPID keys + backend dispatcher. Defer until SMS-for-inquiries
  (Phase 6) ships and we see whether users ask for more channels.
- **Preferred contact channel (call vs WhatsApp vs chat)** —
  recommended in the analysis but blocked on the chat feature
  shipping. Until in-app chat is live, the choice is moot. Revisit
  alongside the chat rollout.
- **Headline / short bio for non-agent users** — nice-to-have,
  no current consumer surface uses it. Add when the inquiry flow
  starts surfacing user identity to agents.
- **"Download my data" (JSON export)** — GDPR-style nice-to-have; add
  when there's a legal/compliance prompt for it.
- **Two-factor on every login** — OTP infra is there, but forcing 2FA
  pre-product-market-fit adds friction. Ship as an opt-in toggle only
  after Phase 5 (active sessions) is live.

---

## Suggested order of merge

1. ✅ Phase 0 (foundations) + Phase 1 (privacy toggle) — same PR.
2. Phase 2 (name + avatar) — unlocks "Edit Profile" button.  ← **next**
3. Phase 4 (hidden listings) — quick win, uses existing data.
4. Phase 5 (active sessions) — depends on the cookie fix already shipped.
5. Phase 3 (saved searches) — higher effort, biggest retention payoff.
6. Phase 6 (notifications) — once we know which channels users actually use.
7. Phase 8 (search defaults) — needs Phase 3's filter plumbing.
8. Phase 7 (password + delete) — security hardening.
9. Phase 9 (phone/email change + OTP hashing) — last, most sensitive.
