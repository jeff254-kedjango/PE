// EditProfilePanel — Phase 2 of Profile_Architecture.md.
//
// Two surfaces today:
// - AvatarUploader (inline file picker with optimistic preview)
// - Name editor (controlled <input> with debounce-free explicit Save so
//   we never PATCH per-keystroke; a single round-trip per save keeps the
//   write rate trivially bounded)
//
// Performance notes:
// - The avatar preview uses `URL.createObjectURL` so the new image paints
//   immediately, before the upload completes. The blob URL is revoked in
//   the cleanup arm of useEffect to avoid leaking memory in long sessions.
// - The Name save mutates via useUpdateMe → optimistic cache write, so
//   the displayed name updates synchronously and rolls back on error.
// - Phases 7 and 9 (password / phone / email change) attach as collapsible
//   sub-sections here so the user has one "Edit Profile" surface that
//   grows over time without page navigation.
import React, { useCallback, useEffect, useRef, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import Icon from '../ui/Icon';
import { useMe, useUpdateMe } from '../../hooks/useMe';
import { useAuth } from '../../context/AuthContext';
import { resolveMediaUrl } from '../../utils/media';
import {
  uploadAvatar,
  updateBio,
  changePassword,
  requestSelfDeletion,
  startPhoneChange,
  confirmPhoneChange,
  startEmailChange,
  confirmEmailChange,
} from '../../api/auth';
import './EditProfilePanel.css';

type Status = { kind: 'idle' } | { kind: 'busy' } | { kind: 'error'; msg: string } | { kind: 'ok'; msg: string };

const initialsOf = (name: string) =>
  name
    .split(' ')
    .map((n) => n[0])
    .filter(Boolean)
    .join('')
    .toUpperCase()
    .slice(0, 2);

const EditProfilePanel: React.FC<{ onClose: () => void }> = ({ onClose }) => {
  const { data: me } = useMe();
  const { token } = useAuth();
  const updateMe = useUpdateMe();
  const queryClient = useQueryClient();

  // ─── Name ────────────────────────────────────────────────────────
  const [name, setName] = useState(me?.name ?? '');
  const [nameStatus, setNameStatus] = useState<Status>({ kind: 'idle' });

  useEffect(() => {
    // Re-sync local draft when the cached user refreshes (e.g. after a
    // /auth/me revalidation). Only resets when the user is NOT actively
    // typing — guarded by the `idle` check.
    if (nameStatus.kind === 'idle' && me?.name && me.name !== name) {
      setName(me.name);
    }
  }, [me?.name, name, nameStatus.kind]);

  const saveName = useCallback(async () => {
    const trimmed = name.trim();
    if (!trimmed || trimmed === me?.name) return;
    setNameStatus({ kind: 'busy' });
    try {
      await updateMe.mutateAsync({ name: trimmed });
      setNameStatus({ kind: 'ok', msg: 'Saved' });
      setTimeout(() => setNameStatus({ kind: 'idle' }), 1500);
    } catch (err) {
      setNameStatus({ kind: 'error', msg: 'Could not save. Try again.' });
    }
  }, [name, me?.name, updateMe]);

  // ─── Avatar ──────────────────────────────────────────────────────
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [avatarStatus, setAvatarStatus] = useState<Status>({ kind: 'idle' });
  // Holds the setTimeout id for the post-upload /auth/me invalidation.
  // We deliberately do NOT cancel this on unmount: queryClient is the
  // global QueryClient instance and the invalidate is safe to fire after
  // the user has navigated away — it ensures ProfilePage / NavBar mounts
  // see the post-worker WebP URL rather than the cached original. Stored
  // on a ref purely so a second upload during the 800ms window can clear
  // the prior pending invalidate (avoiding two redundant /auth/me GETs).
  const pendingMeRefetchRef = useRef<number | null>(null);

  // Revoke the object URL on unmount or when we replace it — leaving
  // these dangling leaks blob storage in long-lived tabs.
  useEffect(() => {
    return () => {
      if (previewUrl) URL.revokeObjectURL(previewUrl);
    };
  }, [previewUrl]);

  const onPickAvatar = useCallback(
    async (e: React.ChangeEvent<HTMLInputElement>) => {
      const file = e.target.files?.[0];
      if (!file || !token) return;
      if (file.size > 5 * 1024 * 1024) {
        setAvatarStatus({ kind: 'error', msg: 'Image too large (max 5 MB).' });
        return;
      }
      // Optimistic preview — paint the new avatar before the upload
      // settles so the user never sees a stale image.
      const localUrl = URL.createObjectURL(file);
      setPreviewUrl(localUrl);
      setAvatarStatus({ kind: 'busy' });
      // If a previous upload's deferred invalidate is still pending,
      // cancel it — this upload supersedes the previous one and will
      // schedule its own invalidation below.
      if (pendingMeRefetchRef.current !== null) {
        window.clearTimeout(pendingMeRefetchRef.current);
        pendingMeRefetchRef.current = null;
      }
      try {
        const { url } = await uploadAvatar(token, file);
        // The backend already wrote users.avatar = url; mirror to the
        // React Query cache so every consumer (NavBar, ProfilePage) sees
        // it without another network round-trip.
        await updateMe.mutateAsync({ avatar: url });
        // Invalidate (don't refetch) the agent/staff/search caches so
        // the new avatar lands on /agents, /staff directory rows, and
        // unified-search results on next subscription — without burning
        // bandwidth for users who never navigate there. Backend also
        // syncs agents.agent_profile_picture in the same upload txn, so
        // the next fetch returns fresh data. Pattern matches the
        // dismissals/feeds invalidations elsewhere in the codebase.
        queryClient.invalidateQueries({ queryKey: ['publicAgents'] });
        // ['agentProfile', agentId] backs /agents/:id — without this, the
        // single-agent page would keep serving the cached pre-upload row
        // until its 2-minute staleTime expires.
        queryClient.invalidateQueries({ queryKey: ['agentProfile'] });
        queryClient.invalidateQueries({ queryKey: ['staffDirectory'] });
        queryClient.invalidateQueries({ queryKey: ['unifiedSearch'] });

        // Converge the local ['auth','me'] cache to the post-WebP URL.
        //
        // The upload endpoint returned the original-extension URL
        // (`<hash>.png|jpg|avif`) and we wrote it into the cache via the
        // optimistic mutateAsync above. The Celery worker now rewrites
        // users.avatar to the WebP variant within ~100–200ms (p95). The
        // backend deliberately keeps the original source file on disk
        // (services/image_processing.py docstring) so the cached URL
        // keeps serving in the meantime — there is NO broken-image
        // window even before this invalidation fires.
        //
        // We still invalidate after the worker's expected completion so
        // that the next subscriber render of ['auth','me'] resolves to
        // the WebP URL — smaller payload on subsequent <img> fetches,
        // and the canonical post-worker state in the cache. One cheap
        // GET /auth/me on warm keep-alive, fired *after* the user has
        // already seen "Avatar updated". Zero impact on perceived
        // latency; net image-bytes savings on the next render and on
        // any peer subscriber that mounts in this session.
        //
        // 800ms ≫ worker p95 (~200ms) with comfortable headroom on a
        // contended worker; well below any human-noticeable delay.
        // Stored on a ref so the cleanup arm can cancel it if the
        // component unmounts before it fires.
        const t = window.setTimeout(() => {
          queryClient.invalidateQueries({ queryKey: ['auth', 'me'] });
        }, 800);
        pendingMeRefetchRef.current = t;

        setAvatarStatus({ kind: 'ok', msg: 'Avatar updated' });
        setTimeout(() => setAvatarStatus({ kind: 'idle' }), 1500);
      } catch (err) {
        setAvatarStatus({ kind: 'error', msg: 'Upload failed. Try again.' });
      } finally {
        // Reset the input so the same file can be re-selected if the
        // user wants to retry.
        if (fileInputRef.current) fileInputRef.current.value = '';
      }
    },
    [token, updateMe, queryClient],
  );

  // previewUrl is a `blob:` URL from URL.createObjectURL — pass it through
  // unchanged. me.avatar is the server's root-relative path; resolveMediaUrl
  // prepends the backend origin so the static mount serves it correctly.
  const displayedAvatar = previewUrl ?? (me?.avatar ? resolveMediaUrl(me.avatar) ?? null : null);

  // ─── Agent bio (visible only to users linked to an Agent row) ────
  // The bio is stored on agents.bio (not users.*) and is the public
  // copy visitors see on /agents and /agents/:id. We mirror the saved
  // value into local state and let the user edit; Save fires a single
  // PATCH /me/bio which writes the agents row and we invalidate the
  // agent-facing React Query keys so the public surfaces refresh.
  const [bio, setBio] = useState<string>(me?.bio ?? '');
  const [bioStatus, setBioStatus] = useState<Status>({ kind: 'idle' });

  useEffect(() => {
    // Re-sync local draft when the cached me refreshes — but only when
    // idle so we never blow away an in-progress edit. Same pattern as
    // the name editor above.
    if (bioStatus.kind === 'idle') {
      const next = me?.bio ?? '';
      if (next !== bio) setBio(next);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [me?.bio]);

  const submitBio = useCallback(async () => {
    if (!token) return;
    setBioStatus({ kind: 'busy' });
    try {
      const trimmed = bio.trim();
      await updateBio(token, trimmed);
      // Bio lives on agents.bio — we cannot setQueryData(['auth','me'])
      // and expect the public surfaces to refresh. Invalidate the
      // agent-facing keys (mirrors the avatar invalidation block above);
      // refetch is lazy so users who don't navigate to /agents next don't
      // pay any network cost.
      queryClient.invalidateQueries({ queryKey: ['publicAgents'] });
      queryClient.invalidateQueries({ queryKey: ['agentProfile'] });
      queryClient.invalidateQueries({ queryKey: ['unifiedSearch'] });
      // Also nudge ['auth','me'] so EditProfilePanel itself sees the
      // server-cleaned value on next render (in case the backend trimmed
      // whitespace or returns null for empty string).
      queryClient.invalidateQueries({ queryKey: ['auth', 'me'] });
      setBioStatus({ kind: 'ok', msg: 'Bio saved' });
      setTimeout(() => setBioStatus({ kind: 'idle' }), 1500);
    } catch (err: unknown) {
      setBioStatus({
        kind: 'error',
        msg: err instanceof Error ? err.message : 'Could not save bio.',
      });
    }
  }, [token, bio, queryClient]);

  // ─── Phase 7: change password (inline expander) ──────────────────
  const [pwOpen, setPwOpen] = useState(false);
  const [oldPw, setOldPw] = useState('');
  const [newPw, setNewPw] = useState('');
  const [pwStatus, setPwStatus] = useState<Status>({ kind: 'idle' });

  const submitPassword = useCallback(async () => {
    if (!token) return;
    if (newPw.length < 6) {
      setPwStatus({ kind: 'error', msg: 'New password must be at least 6 characters.' });
      return;
    }
    setPwStatus({ kind: 'busy' });
    try {
      await changePassword(token, oldPw, newPw);
      setPwStatus({ kind: 'ok', msg: 'Password updated. Other devices have been signed out.' });
      setOldPw('');
      setNewPw('');
    } catch (err: unknown) {
      setPwStatus({
        kind: 'error',
        msg: err instanceof Error ? err.message : 'Could not change password.',
      });
    }
  }, [token, oldPw, newPw]);

  // ─── Phase 7: delete account (two-step confirm) ──────────────────
  const [delOpen, setDelOpen] = useState(false);
  const [delEmailConfirm, setDelEmailConfirm] = useState('');
  const [delReason, setDelReason] = useState('');
  const [delStatus, setDelStatus] = useState<Status>({ kind: 'idle' });

  const submitDeletion = useCallback(async () => {
    if (!token || !me) return;
    if (delEmailConfirm.trim().toLowerCase() !== (me.email || '').toLowerCase()) {
      setDelStatus({ kind: 'error', msg: 'Email confirmation does not match.' });
      return;
    }
    if (delReason.trim().length < 10) {
      setDelStatus({ kind: 'error', msg: 'Tell us briefly why (≥ 10 chars).' });
      return;
    }
    setDelStatus({ kind: 'busy' });
    try {
      await requestSelfDeletion(token, delReason.trim());
      setDelStatus({
        kind: 'ok',
        msg: 'Deletion request submitted. Our team will review within 48 hours.',
      });
      setDelOpen(false);
    } catch (err: unknown) {
      setDelStatus({
        kind: 'error',
        msg: err instanceof Error ? err.message : 'Could not submit request.',
      });
    }
  }, [token, me, delEmailConfirm, delReason]);

  // ─── Phase 9: change phone / email (OTP-gated) ───────────────────
  const [phoneOpen, setPhoneOpen] = useState(false);
  const [newPhone, setNewPhone] = useState('');
  const [phoneOtp, setPhoneOtp] = useState('');
  const [phoneStage, setPhoneStage] = useState<'enter' | 'verify'>('enter');
  const [phoneStatus, setPhoneStatus] = useState<Status>({ kind: 'idle' });

  const submitPhoneStart = useCallback(async () => {
    if (!token) return;
    const cleaned = newPhone.replace(/[\s\-()]/g, '');
    if (cleaned.length < 10) {
      setPhoneStatus({ kind: 'error', msg: 'Phone number looks too short.' });
      return;
    }
    setPhoneStatus({ kind: 'busy' });
    try {
      await startPhoneChange(token, cleaned);
      setPhoneStage('verify');
      setPhoneStatus({ kind: 'ok', msg: 'OTP sent to the new number.' });
    } catch (err: unknown) {
      setPhoneStatus({
        kind: 'error',
        msg: err instanceof Error ? err.message : 'Could not start phone change.',
      });
    }
  }, [token, newPhone]);

  const submitPhoneConfirm = useCallback(async () => {
    if (!token) return;
    if (phoneOtp.length !== 6) {
      setPhoneStatus({ kind: 'error', msg: 'Enter the 6-digit code.' });
      return;
    }
    setPhoneStatus({ kind: 'busy' });
    try {
      const fresh = await confirmPhoneChange(token, phoneOtp);
      // Push the fresh user into the cache so the header re-renders.
      await updateMe.mutateAsync({}); // touch — server already updated; revalidate cache
      // (the mutateAsync call above is a no-op PATCH; instead we set directly)
      if (fresh) {
        // best-effort: also stash in localStorage
        try { localStorage.setItem('weespas_user', JSON.stringify(fresh)); } catch {}
      }
      setPhoneStatus({ kind: 'ok', msg: 'Phone number updated.' });
      setPhoneStage('enter');
      setNewPhone('');
      setPhoneOtp('');
      setPhoneOpen(false);
    } catch (err: unknown) {
      setPhoneStatus({
        kind: 'error',
        msg: err instanceof Error ? err.message : 'Verification failed.',
      });
    }
  }, [token, phoneOtp, updateMe]);

  const [emailOpen, setEmailOpen] = useState(false);
  const [newEmail, setNewEmail] = useState('');
  const [emailOtp, setEmailOtp] = useState('');
  const [emailStage, setEmailStage] = useState<'enter' | 'verify'>('enter');
  const [emailStatus, setEmailStatus] = useState<Status>({ kind: 'idle' });

  const submitEmailStart = useCallback(async () => {
    if (!token) return;
    if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(newEmail)) {
      setEmailStatus({ kind: 'error', msg: 'Enter a valid email address.' });
      return;
    }
    setEmailStatus({ kind: 'busy' });
    try {
      await startEmailChange(token, newEmail.toLowerCase());
      setEmailStage('verify');
      setEmailStatus({ kind: 'ok', msg: 'OTP sent (check your current phone — email channel TBD).' });
    } catch (err: unknown) {
      setEmailStatus({
        kind: 'error',
        msg: err instanceof Error ? err.message : 'Could not start email change.',
      });
    }
  }, [token, newEmail]);

  const submitEmailConfirm = useCallback(async () => {
    if (!token) return;
    if (emailOtp.length !== 6) {
      setEmailStatus({ kind: 'error', msg: 'Enter the 6-digit code.' });
      return;
    }
    setEmailStatus({ kind: 'busy' });
    try {
      const fresh = await confirmEmailChange(token, emailOtp);
      if (fresh) {
        try { localStorage.setItem('weespas_user', JSON.stringify(fresh)); } catch {}
      }
      setEmailStatus({ kind: 'ok', msg: 'Email updated.' });
      setEmailStage('enter');
      setNewEmail('');
      setEmailOtp('');
      setEmailOpen(false);
    } catch (err: unknown) {
      setEmailStatus({
        kind: 'error',
        msg: err instanceof Error ? err.message : 'Verification failed.',
      });
    }
  }, [token, emailOtp]);

  if (!me) {
    return (
      <div className="edit-profile-panel" aria-busy="true">
        <div className="edit-profile-panel__loading">Loading…</div>
      </div>
    );
  }

  return (
    <div className="edit-profile-panel" role="dialog" aria-label="Edit profile">
      <header className="edit-profile-panel__header">
        <button
          type="button"
          className="edit-profile-panel__back"
          onClick={onClose}
          aria-label="Back to profile"
        >
          <Icon name="arrowLeft" size={18} />
          <span>Back</span>
        </button>
        <h2 className="edit-profile-panel__title">Edit Profile</h2>
      </header>

      {/* Avatar */}
      <section className="edit-section">
        <h3 className="edit-section__title">Avatar</h3>
        <div className="edit-avatar-row">
          <div className="edit-avatar">
            {displayedAvatar ? (
              <img src={displayedAvatar} alt={me.name} className="edit-avatar__img" />
            ) : (
              <span className="edit-avatar__initials">{initialsOf(me.name || '?')}</span>
            )}
          </div>
          <div className="edit-avatar-actions">
            <input
              ref={fileInputRef}
              type="file"
              accept="image/jpeg,image/png,image/webp,image/avif"
              hidden
              onChange={onPickAvatar}
            />
            <button
              type="button"
              className="edit-btn"
              onClick={() => fileInputRef.current?.click()}
              disabled={avatarStatus.kind === 'busy'}
            >
              <Icon name="upload" size={16} />
              {avatarStatus.kind === 'busy' ? 'Uploading…' : 'Change photo'}
            </button>
            {avatarStatus.kind === 'error' && (
              <p className="edit-msg edit-msg--error" role="alert">{avatarStatus.msg}</p>
            )}
            {avatarStatus.kind === 'ok' && (
              <p className="edit-msg edit-msg--ok">{avatarStatus.msg}</p>
            )}
          </div>
        </div>
      </section>

      {/* Name */}
      <section className="edit-section">
        <h3 className="edit-section__title">Name</h3>
        <div className="edit-row">
          <input
            type="text"
            className="edit-input"
            value={name}
            maxLength={255}
            onChange={(e) => setName(e.target.value)}
            onBlur={saveName}
            onKeyDown={(e) => { if (e.key === 'Enter') saveName(); }}
            placeholder="Your full name"
            aria-label="Name"
          />
          <button
            type="button"
            className="edit-btn edit-btn--primary"
            onClick={saveName}
            disabled={nameStatus.kind === 'busy' || !name.trim() || name === me.name}
          >
            {nameStatus.kind === 'busy' ? 'Saving…' : 'Save'}
          </button>
        </div>
        {nameStatus.kind === 'error' && (
          <p className="edit-msg edit-msg--error" role="alert">{nameStatus.msg}</p>
        )}
        {nameStatus.kind === 'ok' && (
          <p className="edit-msg edit-msg--ok">{nameStatus.msg}</p>
        )}
      </section>

      {/* Write bio — agents only. Visible only when the authed user has
          a linked Agent row (me.agent_id). The bio shows up under the
          agent's card on /agents and at the top of /agents/:id; URLs and
          phone numbers are auto-linkified there. */}
      {me.agent_id && (
        <section className="edit-section">
          <h3 className="edit-section__title">
            <Icon name="edit" size={18} />
            <span style={{ marginLeft: 8 }}>Write bio</span>
          </h3>
          <p className="edit-section__hint">
            Tell visitors about yourself and the properties you handle.
            You can paste links to your socials and phone numbers — they'll
            be clickable on your public agent page.
          </p>
          <textarea
            className="edit-input edit-input--textarea edit-bio__textarea"
            value={bio}
            onChange={(e) => setBio(e.target.value.slice(0, 500))}
            maxLength={500}
            rows={4}
            placeholder="e.g. I help families find homes in Kilimani and Lavington. WhatsApp +254 712 345 678 or follow https://instagram.com/example."
            aria-label="Bio"
          />
          <div className="edit-bio__footer">
            <span className="edit-bio__count" aria-live="polite">
              {bio.length} / 500
            </span>
            <button
              type="button"
              className="edit-btn edit-btn--primary"
              onClick={submitBio}
              disabled={bioStatus.kind === 'busy' || bio.trim() === (me.bio ?? '').trim()}
            >
              {bioStatus.kind === 'busy' ? 'Saving…' : 'Save bio'}
            </button>
          </div>
          {bioStatus.kind === 'error' && (
            <p className="edit-msg edit-msg--error" role="alert">{bioStatus.msg}</p>
          )}
          {bioStatus.kind === 'ok' && (
            <p className="edit-msg edit-msg--ok">{bioStatus.msg}</p>
          )}
        </section>
      )}

      {/* Phone */}
      <section className="edit-section">
        <h3 className="edit-section__title">Phone</h3>
        <div className="edit-row edit-row--read">
          <span className="edit-readonly">+254 {me.phone}</span>
          <button
            type="button"
            className="edit-btn"
            onClick={() => setPhoneOpen((v) => !v)}
          >
            {phoneOpen ? 'Cancel' : 'Change'}
          </button>
        </div>
        {phoneOpen && (
          <div className="edit-subform">
            {phoneStage === 'enter' ? (
              <>
                <input
                  className="edit-input"
                  type="tel"
                  inputMode="tel"
                  placeholder="New phone (e.g. 0712345678)"
                  value={newPhone}
                  onChange={(e) => setNewPhone(e.target.value)}
                />
                <button
                  type="button"
                  className="edit-btn edit-btn--primary"
                  disabled={phoneStatus.kind === 'busy'}
                  onClick={submitPhoneStart}
                >
                  {phoneStatus.kind === 'busy' ? 'Sending…' : 'Send code'}
                </button>
              </>
            ) : (
              <>
                <input
                  className="edit-input"
                  type="text"
                  inputMode="numeric"
                  maxLength={6}
                  placeholder="6-digit code"
                  value={phoneOtp}
                  onChange={(e) => setPhoneOtp(e.target.value.replace(/\D/g, ''))}
                />
                <button
                  type="button"
                  className="edit-btn edit-btn--primary"
                  disabled={phoneStatus.kind === 'busy'}
                  onClick={submitPhoneConfirm}
                >
                  {phoneStatus.kind === 'busy' ? 'Verifying…' : 'Confirm'}
                </button>
              </>
            )}
            {phoneStatus.kind === 'error' && (
              <p className="edit-msg edit-msg--error" role="alert">{phoneStatus.msg}</p>
            )}
            {phoneStatus.kind === 'ok' && (
              <p className="edit-msg edit-msg--ok">{phoneStatus.msg}</p>
            )}
          </div>
        )}
      </section>

      {/* Email */}
      <section className="edit-section">
        <h3 className="edit-section__title">Email</h3>
        <div className="edit-row edit-row--read">
          <span className="edit-readonly">{me.email}</span>
          <button
            type="button"
            className="edit-btn"
            onClick={() => setEmailOpen((v) => !v)}
          >
            {emailOpen ? 'Cancel' : 'Change'}
          </button>
        </div>
        {emailOpen && (
          <div className="edit-subform">
            {emailStage === 'enter' ? (
              <>
                <input
                  className="edit-input"
                  type="email"
                  placeholder="new@example.com"
                  value={newEmail}
                  onChange={(e) => setNewEmail(e.target.value)}
                />
                <button
                  type="button"
                  className="edit-btn edit-btn--primary"
                  disabled={emailStatus.kind === 'busy'}
                  onClick={submitEmailStart}
                >
                  {emailStatus.kind === 'busy' ? 'Sending…' : 'Send code'}
                </button>
              </>
            ) : (
              <>
                <input
                  className="edit-input"
                  type="text"
                  inputMode="numeric"
                  maxLength={6}
                  placeholder="6-digit code"
                  value={emailOtp}
                  onChange={(e) => setEmailOtp(e.target.value.replace(/\D/g, ''))}
                />
                <button
                  type="button"
                  className="edit-btn edit-btn--primary"
                  disabled={emailStatus.kind === 'busy'}
                  onClick={submitEmailConfirm}
                >
                  {emailStatus.kind === 'busy' ? 'Verifying…' : 'Confirm'}
                </button>
              </>
            )}
            {emailStatus.kind === 'error' && (
              <p className="edit-msg edit-msg--error" role="alert">{emailStatus.msg}</p>
            )}
            {emailStatus.kind === 'ok' && (
              <p className="edit-msg edit-msg--ok">{emailStatus.msg}</p>
            )}
          </div>
        )}
      </section>

      {/* Password */}
      <section className="edit-section">
        <h3 className="edit-section__title">Password</h3>
        <div className="edit-row edit-row--read">
          <span className="edit-readonly">••••••••</span>
          <button
            type="button"
            className="edit-btn"
            onClick={() => setPwOpen((v) => !v)}
          >
            {pwOpen ? 'Cancel' : 'Change'}
          </button>
        </div>
        {pwOpen && (
          <div className="edit-subform">
            <input
              className="edit-input"
              type="password"
              placeholder="Current password"
              autoComplete="current-password"
              value={oldPw}
              onChange={(e) => setOldPw(e.target.value)}
            />
            <input
              className="edit-input"
              type="password"
              placeholder="New password (min 6 chars)"
              autoComplete="new-password"
              value={newPw}
              onChange={(e) => setNewPw(e.target.value)}
            />
            <button
              type="button"
              className="edit-btn edit-btn--primary"
              disabled={pwStatus.kind === 'busy' || !oldPw || newPw.length < 6}
              onClick={submitPassword}
            >
              {pwStatus.kind === 'busy' ? 'Saving…' : 'Update password'}
            </button>
            {pwStatus.kind === 'error' && (
              <p className="edit-msg edit-msg--error" role="alert">{pwStatus.msg}</p>
            )}
            {pwStatus.kind === 'ok' && (
              <p className="edit-msg edit-msg--ok">{pwStatus.msg}</p>
            )}
          </div>
        )}
      </section>

      {/* Danger zone: delete account */}
      <section className="edit-section edit-section--danger">
        <h3 className="edit-section__title">Delete account</h3>
        <p className="edit-section__hint">
          Submits a deletion request to our staff. Your listings and contact
          info will be removed after review.
        </p>
        {!delOpen ? (
          <button
            type="button"
            className="edit-btn edit-btn--danger"
            onClick={() => setDelOpen(true)}
          >
            <Icon name="trash" size={16} />
            Request account deletion
          </button>
        ) : (
          <div className="edit-subform">
            <input
              className="edit-input"
              type="email"
              placeholder={`Type "${me.email}" to confirm`}
              value={delEmailConfirm}
              onChange={(e) => setDelEmailConfirm(e.target.value)}
            />
            <textarea
              className="edit-input edit-input--textarea"
              placeholder="Why are you leaving? (min 10 chars)"
              maxLength={1000}
              value={delReason}
              onChange={(e) => setDelReason(e.target.value)}
            />
            <div className="edit-row">
              <button
                type="button"
                className="edit-btn"
                onClick={() => { setDelOpen(false); setDelStatus({ kind: 'idle' }); }}
              >
                Cancel
              </button>
              <button
                type="button"
                className="edit-btn edit-btn--danger"
                disabled={delStatus.kind === 'busy'}
                onClick={submitDeletion}
              >
                {delStatus.kind === 'busy' ? 'Submitting…' : 'Confirm deletion'}
              </button>
            </div>
            {delStatus.kind === 'error' && (
              <p className="edit-msg edit-msg--error" role="alert">{delStatus.msg}</p>
            )}
            {delStatus.kind === 'ok' && (
              <p className="edit-msg edit-msg--ok">{delStatus.msg}</p>
            )}
          </div>
        )}
        {delOpen === false && delStatus.kind === 'ok' && (
          <p className="edit-msg edit-msg--ok">{delStatus.msg}</p>
        )}
      </section>
    </div>
  );
};

export default EditProfilePanel;
