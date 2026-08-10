// PreferencesPanel — Phases 1, 4, 5, 6, 8 of Profile_Architecture.md.
//
// Sections (in order): Privacy, Search defaults, Notifications, Hidden
// listings, Active sessions. Each section is rendered inline (no extra
// route, no extra lazy boundary) because the cost of the markup is
// trivial compared to the bundle-split overhead of N tiny chunks.
//
// Data fetching strategy:
// - Dismissals & sessions are React Query keys with a short staleTime
//   so opening the panel twice in quick succession hits the cache.
// - Preference toggles (privacy, notifications, search defaults) write
//   through useUpdateMe → optimistic ['auth', 'me'] cache, no extra GET.
import React, { useCallback, useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import Icon from '../ui/Icon';
import { useMe, useUpdateMe } from '../../hooks/useMe';
import { useAuth } from '../../context/AuthContext';
import {
  fetchHiddenListings,
  unhideAllDismissals,
  fetchSessions,
  revokeSession,
  revokeAllOtherSessions,
  type HiddenListingItem,
  type ActiveSessionItem,
} from '../../api/auth';
import { removeDismissal } from '../../api/dismissals';
import {
  useSavedSearches,
  useDeleteSavedSearch,
  useTouchSavedSearch,
} from '../../hooks/useSavedSearches';
import { useNavigate } from 'react-router-dom';
import './PreferencesPanel.css';

const DISMISSALS_KEY = ['me', 'dismissals'] as const;
const SESSIONS_KEY = ['me', 'sessions'] as const;

// Tiny UA parser — covers ~95% of real traffic with 30 lines. Far cheaper
// than ua-parser-js (which ships 50+ KB minified) and runs synchronously
// in the same tick as the render, so list rendering stays O(n) without
// any async/effect hop.
function parseUA(ua: string | null | undefined): { browser: string; os: string } {
  if (!ua) return { browser: 'Unknown', os: 'Unknown' };
  const s = ua;
  let browser = 'Unknown';
  if (/Edg\//.test(s)) browser = 'Edge';
  else if (/OPR\/|Opera/.test(s)) browser = 'Opera';
  else if (/Chrome\/[0-9]/.test(s)) browser = 'Chrome';
  else if (/Firefox\//.test(s)) browser = 'Firefox';
  else if (/Safari\//.test(s)) browser = 'Safari';
  let os = 'Unknown';
  if (/Windows NT/.test(s)) os = 'Windows';
  else if (/Android/.test(s)) os = 'Android';
  else if (/(iPhone|iPad|iOS)/.test(s)) os = 'iOS';
  else if (/Mac OS X/.test(s)) os = 'macOS';
  else if (/Linux/.test(s)) os = 'Linux';
  return { browser, os };
}

function timeAgo(iso: string | null | undefined): string {
  if (!iso) return '—';
  const ms = Date.now() - new Date(iso).getTime();
  if (ms < 60_000) return 'just now';
  const m = Math.floor(ms / 60_000);
  if (m < 60) return `${m} min ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  const d = Math.floor(h / 24);
  return `${d}d ago`;
}

// ─── Reusable toggle row ────────────────────────────────────────────
const ToggleRow: React.FC<{
  label: string;
  hint?: string;
  checked: boolean;
  disabled?: boolean;
  onChange: () => void;
  badge?: string;
}> = ({ label, hint, checked, disabled, onChange, badge }) => (
  <label className="prefs-toggle">
    <div className="prefs-toggle__copy">
      <span className="prefs-toggle__label">
        {label}
        {badge && <span className="prefs-toggle__badge">{badge}</span>}
      </span>
      {hint && <span className="prefs-toggle__hint">{hint}</span>}
    </div>
    <input
      type="checkbox"
      className="prefs-toggle__input"
      checked={checked}
      onChange={onChange}
      disabled={disabled}
      aria-label={label}
    />
    <span className="prefs-toggle__switch" aria-hidden="true" />
  </label>
);

const PreferencesPanel: React.FC<{ onClose: () => void }> = ({ onClose }) => {
  const { data: me } = useMe();
  const updateMe = useUpdateMe();
  const { token } = useAuth();
  const queryClient = useQueryClient();

  // ─── Privacy ──────────────────────────────────────────────────────
  const isPublic = me?.is_public_profile ?? false;
  const togglePublic = useCallback(() => {
    updateMe.mutate({ is_public_profile: !isPublic });
  }, [isPublic, updateMe]);

  // ─── Notifications (Phase 6) ─────────────────────────────────────
  // The user model carries these as nullable booleans; treat missing
  // as the documented default (true for SMS-on-inquiry, false for the rest).
  const notifyInquirySms = (me as { notify_inquiries_sms?: boolean })?.notify_inquiries_sms ?? true;
  const notifyInquiryEmail = (me as { notify_inquiries_email?: boolean })?.notify_inquiries_email ?? false;
  const notifyDigestEmail = (me as { notify_digest_email?: boolean })?.notify_digest_email ?? false;
  const notifyPush = (me as { notify_push?: boolean })?.notify_push ?? false;

  // ─── Search defaults (Phase 8) ───────────────────────────────────
  const defaultRadius = (me as { default_radius_km?: number | null })?.default_radius_km ?? 10;
  const preferredListing = (me as { preferred_listing_type?: 'rent' | 'sale' | null })?.preferred_listing_type ?? null;
  const language = (me as { language?: 'en' | 'sw' | null })?.language ?? 'en';

  // ─── Hidden listings (Phase 4) ───────────────────────────────────
  const hidden = useQuery<HiddenListingItem[], Error>({
    queryKey: DISMISSALS_KEY,
    queryFn: () => fetchHiddenListings(token!),
    enabled: !!token,
    staleTime: 60_000,
  });

  const unhideOne = useMutation({
    mutationFn: (propertyId: string) => removeDismissal(token!, propertyId),
    onMutate: async (pid) => {
      await queryClient.cancelQueries({ queryKey: DISMISSALS_KEY });
      const prev = queryClient.getQueryData<HiddenListingItem[]>(DISMISSALS_KEY) ?? [];
      queryClient.setQueryData<HiddenListingItem[]>(
        DISMISSALS_KEY,
        prev.filter((h) => h.property_id !== pid),
      );
      return { prev };
    },
    onError: (_e, _v, ctx) => {
      if (ctx?.prev) queryClient.setQueryData(DISMISSALS_KEY, ctx.prev);
    },
    onSettled: () => {
      queryClient.invalidateQueries({ queryKey: DISMISSALS_KEY });
      // The home feed depends on dismissals → invalidate that too so the
      // unhidden card reappears without a manual reload.
      queryClient.invalidateQueries({ queryKey: ['properties'] });
    },
  });

  const unhideAll = useMutation({
    mutationFn: () => unhideAllDismissals(token!),
    onMutate: async () => {
      await queryClient.cancelQueries({ queryKey: DISMISSALS_KEY });
      const prev = queryClient.getQueryData<HiddenListingItem[]>(DISMISSALS_KEY) ?? [];
      queryClient.setQueryData<HiddenListingItem[]>(DISMISSALS_KEY, []);
      return { prev };
    },
    onError: (_e, _v, ctx) => {
      if (ctx?.prev) queryClient.setQueryData(DISMISSALS_KEY, ctx.prev);
    },
    onSettled: () => {
      queryClient.invalidateQueries({ queryKey: DISMISSALS_KEY });
      queryClient.invalidateQueries({ queryKey: ['properties'] });
    },
  });

  // ─── Active sessions (Phase 5) ───────────────────────────────────
  const sessionsQ = useQuery<ActiveSessionItem[], Error>({
    queryKey: SESSIONS_KEY,
    queryFn: () => fetchSessions(token!),
    enabled: !!token,
    staleTime: 30_000,
  });

  const revokeOne = useMutation({
    mutationFn: (id: string) => revokeSession(token!, id),
    onMutate: async (id) => {
      await queryClient.cancelQueries({ queryKey: SESSIONS_KEY });
      const prev = queryClient.getQueryData<ActiveSessionItem[]>(SESSIONS_KEY) ?? [];
      queryClient.setQueryData<ActiveSessionItem[]>(
        SESSIONS_KEY,
        prev.filter((s) => s.id !== id),
      );
      return { prev };
    },
    onError: (_e, _v, ctx) => {
      if (ctx?.prev) queryClient.setQueryData(SESSIONS_KEY, ctx.prev);
    },
    onSettled: () => queryClient.invalidateQueries({ queryKey: SESSIONS_KEY }),
  });

  const [showRevokeAllConfirm, setShowRevokeAllConfirm] = useState(false);
  const revokeAll = useMutation({
    mutationFn: () => revokeAllOtherSessions(token!),
    onSuccess: () => {
      setShowRevokeAllConfirm(false);
      queryClient.invalidateQueries({ queryKey: SESSIONS_KEY });
    },
  });

  // ─── Notification mutations (each as its own bound callback so React
  // doesn't allocate a fresh closure per toggle on every render) ─────
  const toggleField = useCallback(
    (
      field:
        | 'notify_inquiries_sms'
        | 'notify_inquiries_email'
        | 'notify_digest_email'
        | 'notify_push',
      current: boolean,
    ) => updateMe.mutate({ [field]: !current }),
    [updateMe],
  );

  const radiusOptions = useMemo(() => [5, 10, 25, 50], []);

  // ─── Saved searches (Phase 3) ────────────────────────────────────
  const navigate = useNavigate();
  const savedSearches = useSavedSearches();
  const deleteSearch = useDeleteSavedSearch();
  const touchSearch = useTouchSavedSearch();

  const applySavedSearch = useCallback(
    (id: string, filters: Record<string, unknown>) => {
      // Re-hydrate the URL filter shape. We push to "/" with a query
      // string so `useFilterParams` picks it up on the home page mount —
      // no special-case render path here, just a navigation.
      const qs = new URLSearchParams();
      Object.entries(filters || {}).forEach(([k, v]) => {
        if (v == null || v === '') return;
        if (Array.isArray(v)) {
          v.forEach((item) => qs.append(k, String(item)));
        } else {
          qs.set(k, String(v));
        }
      });
      // Fire-and-forget touch — the navigation doesn't wait on it.
      touchSearch.mutate(id);
      navigate(qs.toString() ? `/?${qs.toString()}` : '/');
    },
    [navigate, touchSearch],
  );

  return (
    <div className="prefs-panel" role="dialog" aria-label="Preferences">
      <header className="prefs-panel__header">
        <button
          type="button"
          className="prefs-panel__back"
          onClick={onClose}
          aria-label="Back to profile"
        >
          <Icon name="arrowLeft" size={18} />
          <span>Back</span>
        </button>
        <h2 className="prefs-panel__title">Preferences</h2>
      </header>

      {/* Privacy */}
      <section className="prefs-section">
        <h3 className="prefs-section__title">Privacy</h3>
        <ToggleRow
          label="Show my phone & email on my public profile"
          hint="When off, only your name and avatar are visible to other users."
          checked={isPublic}
          disabled={updateMe.isPending}
          onChange={togglePublic}
        />
        {updateMe.isError && (
          <p className="prefs-error" role="alert">Couldn't save that. Please try again.</p>
        )}
      </section>

      {/* Search defaults */}
      <section className="prefs-section">
        <h3 className="prefs-section__title">Search defaults</h3>
        <div className="prefs-field">
          <span className="prefs-field__label">Default search radius</span>
          <div className="prefs-chip-row">
            {radiusOptions.map((km) => (
              <button
                key={km}
                type="button"
                className={`prefs-chip ${defaultRadius === km ? 'prefs-chip--on' : ''}`}
                onClick={() => updateMe.mutate({ default_radius_km: km })}
                disabled={updateMe.isPending}
              >
                {km} km
              </button>
            ))}
          </div>
        </div>
        <div className="prefs-field">
          <span className="prefs-field__label">Preferred listing type</span>
          <div className="prefs-chip-row">
            {(['rent', 'sale'] as const).map((t) => (
              <button
                key={t}
                type="button"
                className={`prefs-chip ${preferredListing === t ? 'prefs-chip--on' : ''}`}
                onClick={() =>
                  updateMe.mutate({ preferred_listing_type: preferredListing === t ? null : t })
                }
                disabled={updateMe.isPending}
              >
                {t === 'rent' ? 'Rent' : 'For sale'}
              </button>
            ))}
          </div>
        </div>
        <div className="prefs-field">
          <span className="prefs-field__label">Language</span>
          <div className="prefs-chip-row">
            {(['en', 'sw'] as const).map((l) => (
              <button
                key={l}
                type="button"
                className={`prefs-chip ${language === l ? 'prefs-chip--on' : ''}`}
                onClick={() => updateMe.mutate({ language: l })}
                disabled={updateMe.isPending}
              >
                {l === 'en' ? 'English' : 'Kiswahili'}
              </button>
            ))}
          </div>
        </div>
      </section>

      {/* Saved searches */}
      <section className="prefs-section">
        <h3 className="prefs-section__title">
          Saved searches
          {(savedSearches.data?.length ?? 0) > 0 && (
            <span className="prefs-section__count"> ({savedSearches.data!.length})</span>
          )}
        </h3>
        {savedSearches.isLoading ? (
          <p className="prefs-empty">Loading…</p>
        ) : (savedSearches.data?.length ?? 0) === 0 ? (
          <p className="prefs-empty">
            No saved searches yet. Save your current filters from the home page to access them here.
          </p>
        ) : (
          <div className="prefs-saved-list">
            {savedSearches.data!.map((s) => (
              <div key={s.id} className="prefs-saved-row">
                <button
                  type="button"
                  className="prefs-saved-apply"
                  onClick={() => applySavedSearch(s.id, s.filters)}
                  title="Apply this search"
                >
                  <Icon name="search" size={14} />
                  <span>{s.name}</span>
                </button>
                <button
                  type="button"
                  className="prefs-link-btn"
                  onClick={() => deleteSearch.mutate(s.id)}
                  aria-label={`Delete saved search ${s.name}`}
                >
                  <Icon name="trash" size={14} />
                </button>
              </div>
            ))}
          </div>
        )}
      </section>

      {/* Notifications */}
      <section className="prefs-section">
        <h3 className="prefs-section__title">Notifications</h3>
        <ToggleRow
          label="SMS when someone inquires about my listing"
          hint="Agents only — fires when a contact form is submitted on one of your properties."
          checked={notifyInquirySms}
          disabled={updateMe.isPending}
          onChange={() => toggleField('notify_inquiries_sms', notifyInquirySms)}
        />
        <ToggleRow
          label="Email when someone inquires about my listing"
          checked={notifyInquiryEmail}
          disabled={updateMe.isPending}
          onChange={() => toggleField('notify_inquiries_email', notifyInquiryEmail)}
          badge="Coming soon"
        />
        <ToggleRow
          label="Weekly email digest of new listings matching my filters"
          checked={notifyDigestEmail}
          disabled={updateMe.isPending}
          onChange={() => toggleField('notify_digest_email', notifyDigestEmail)}
          badge="Coming soon"
        />
        <ToggleRow
          label="Browser push notifications"
          checked={notifyPush}
          disabled={updateMe.isPending}
          onChange={() => toggleField('notify_push', notifyPush)}
          badge="Coming soon"
        />
      </section>

      {/* Hidden listings */}
      <section className="prefs-section">
        <h3 className="prefs-section__title">
          Hidden listings
          {(hidden.data?.length ?? 0) > 0 && (
            <span className="prefs-section__count"> ({hidden.data!.length})</span>
          )}
        </h3>
        {hidden.isLoading ? (
          <p className="prefs-empty">Loading…</p>
        ) : (hidden.data?.length ?? 0) === 0 ? (
          <p className="prefs-empty">You haven't hidden any properties.</p>
        ) : (
          <>
            <div className="prefs-hidden-list">
              {hidden.data!.map((row) => (
                <div key={row.property_id} className="prefs-hidden-row">
                  {row.main_image_url ? (
                    <img
                      src={row.main_image_url}
                      alt=""
                      className="prefs-hidden-thumb"
                      loading="lazy"
                      decoding="async"
                    />
                  ) : (
                    <div className="prefs-hidden-thumb prefs-hidden-thumb--placeholder">
                      <Icon name="image" size={20} />
                    </div>
                  )}
                  <div className="prefs-hidden-meta">
                    <span className="prefs-hidden-title">{row.title ?? 'Untitled listing'}</span>
                    <span className="prefs-hidden-sub">
                      {row.city ?? '—'}
                      {row.price != null ? ` · KES ${row.price.toLocaleString()}` : ''}
                    </span>
                  </div>
                  <button
                    type="button"
                    className="prefs-link-btn"
                    onClick={() => unhideOne.mutate(row.property_id)}
                  >
                    Unhide
                  </button>
                </div>
              ))}
            </div>
            <button
              type="button"
              className="prefs-secondary-btn"
              onClick={() => unhideAll.mutate()}
              disabled={unhideAll.isPending}
            >
              Unhide all
            </button>
          </>
        )}
      </section>

      {/* Active sessions */}
      <section className="prefs-section">
        <h3 className="prefs-section__title">Active devices</h3>
        {sessionsQ.isLoading ? (
          <p className="prefs-empty">Loading…</p>
        ) : (sessionsQ.data?.length ?? 0) === 0 ? (
          <p className="prefs-empty">No active sessions found.</p>
        ) : (
          <>
            <div className="prefs-sessions">
              {sessionsQ.data!.map((s) => {
                const ua = parseUA(s.user_agent);
                return (
                  <div key={s.id} className={`prefs-session ${s.is_current ? 'prefs-session--current' : ''}`}>
                    <div className="prefs-session__main">
                      <span className="prefs-session__device">
                        {ua.browser} on {ua.os}
                        {s.is_current && <span className="prefs-session__current">Current</span>}
                      </span>
                      <span className="prefs-session__meta">
                        {s.geo_city ?? s.geo_county ?? s.ip_address ?? '—'} · {timeAgo(s.last_seen_at)}
                      </span>
                    </div>
                    {!s.is_current && (
                      <button
                        type="button"
                        className="prefs-link-btn"
                        onClick={() => revokeOne.mutate(s.id)}
                      >
                        Sign out
                      </button>
                    )}
                  </div>
                );
              })}
            </div>
            {!showRevokeAllConfirm ? (
              <button
                type="button"
                className="prefs-secondary-btn"
                onClick={() => setShowRevokeAllConfirm(true)}
              >
                Sign out of all other devices
              </button>
            ) : (
              <div className="prefs-confirm">
                <span>Sign out every other device?</span>
                <button
                  type="button"
                  className="prefs-link-btn"
                  onClick={() => setShowRevokeAllConfirm(false)}
                >
                  Cancel
                </button>
                <button
                  type="button"
                  className="prefs-secondary-btn prefs-secondary-btn--danger"
                  onClick={() => revokeAll.mutate()}
                  disabled={revokeAll.isPending}
                >
                  Confirm
                </button>
              </div>
            )}
          </>
        )}
      </section>
    </div>
  );
};

export default PreferencesPanel;
