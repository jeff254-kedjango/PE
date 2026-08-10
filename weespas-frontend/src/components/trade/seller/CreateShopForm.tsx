// CreateShopForm — open a shop (the seller's storefront anchor). A shop is a named point on the
// map; its listings inherit that location for the proximity feed. "Every house a shop" (§9): any
// signed-in user can create one.
//
// §8 handle: an OPTIONAL shareable URL slug (/shop/<handle>) can be claimed here. Optional so
// shop creation is never blocked on the handle UX — a shop without one still has a shareable
// /shop/<sellerId> URL from day one. When set, the handle is claimed AFTER the shop is created
// (a two-step commit): if the claim fails, the shop still exists and the toast surfaces the
// specific reason (handle-taken / handle-syntax / …) so the seller can retry from the dashboard.
import React, { useEffect, useMemo, useState } from 'react';
import { useToast } from '../../../context/ToastContext';
import { useDebounce } from '../../../hooks/useDebounce';
import { useGeolocation } from '../../../hooks/useGeolocation';
import { useClaimShopHandle, useCreateShop } from '../../../hooks/useSellerMutations';
import {
  checkHandleAvailable, claimShopHandle, uploadTradeMedia,
  type CommerceSession, type HandleAvailability, type HandleReason,
} from '../../../api/commerce';
import { CATEGORY_META, CATEGORY_SLUGS } from '../../../utils/categories';
import SellerModal from './SellerModal';
import ShopImagePicker from './ShopImagePicker';

// ─────────────────────────── handle helpers (module-scoped, pure) ───────────────────────────
// Cheap client-side pre-syntax check so we can flash inline red without waiting on the debounce
// probe. Mirrors services.shops.normalize_and_validate_handle EXACTLY (kebab-case, 3–30 chars,
// no leading/trailing/double hyphens). The server remains the authority — this is a UX-only
// gate that keeps the probe from firing for obvious garbage. Case-folds first so the visible
// error matches what the server would say.
const HANDLE_MIN = 3;
const HANDLE_MAX = 30;
const HANDLE_RE = /^[a-z0-9](?:[a-z0-9]|-(?=[a-z0-9]))*[a-z0-9]$|^[a-z0-9]$/;
// Small reserved-word set — must match the server's _RESERVED_HANDLES. Kept explicit; a drift
// here just means the probe surfaces 'handle-reserved' at the server round-trip instead of
// pre-emptively, which is a degrade, not a break.
const RESERVED_HANDLES: ReadonlySet<string> = new Set([
  'mine', 'admin', 'api', 'new', 'shop', 'shops', 'sellers', 'seller',
  'storefront', 'me', 'settings', 'login', 'signup', 'about', 'help',
]);

/** Client-side pre-syntax check. Returns null when the trimmed+lowered value looks legal; a
 *  reason slug otherwise. Same slugs the server uses so the inline copy map is single-source. */
function preSyntax(raw: string): HandleReason | null {
  const t = raw.trim().toLowerCase();
  if (!t) return 'handle-required';
  if (RESERVED_HANDLES.has(t)) return 'handle-reserved';
  if (t.length < HANDLE_MIN || t.length > HANDLE_MAX) return 'handle-length';
  if (!HANDLE_RE.test(t)) return 'handle-syntax';
  return null;
}

/** Reason → seller-facing inline copy. Kept short and specific — "3–30 chars" is more useful than
 *  "invalid length" (the seller can see the fix). "Taken" is stated plainly; a "try another"
 *  microcopy is added on the input's hint row, not here. */
const REASON_COPY: Record<HandleReason, string> = {
  'handle-required': 'Enter a handle, or leave blank to skip.',
  'handle-length': 'Use 3–30 characters.',
  'handle-syntax': 'Letters, numbers and single hyphens only (no leading/trailing/double hyphen).',
  'handle-reserved': 'That name is reserved by the platform. Try another.',
  'handle-taken': 'That handle is taken. Try another.',
  'handle-locked': 'This shop already has a handle. It can\'t be changed.',
};

interface CreateShopFormProps {
  session: CommerceSession | null;
  /** Weespas session token — used ONLY to upload the logo/banner (the two-token exception). */
  weespasToken?: string | null;
  onClose: () => void;
  onCreated?: (shopId: string) => void;
}

const CreateShopForm: React.FC<CreateShopFormProps> = ({ session, weespasToken, onClose, onCreated }) => {
  const { toast } = useToast();
  const [name, setName] = useState('');
  const [displayName, setDisplayName] = useState('');
  // Optional trade category (drives the trending rail color). '' = unset (sent as undefined).
  const [category, setCategory] = useState('');
  // Optional shareable handle (§8 storefront: /shop/<handle>). '' = skipped (the shop still gets
  // a /shop/<sellerId> URL). Kept as a plain string; normalization to lowercase happens at the
  // pre-syntax check + on the server.
  const [handle, setHandle] = useState('');
  // Server-side availability answer (null while the probe hasn't run for the current input yet).
  // The debounce means `handle` may lead `probe.handle` by a keystroke — the render below reads
  // `probe` only when it matches the debounced value, so the badge never lies about stale input.
  const [probe, setProbe] = useState<HandleAvailability | null>(null);
  const [probing, setProbing] = useState(false);
  const [lat, setLat] = useState('');
  const [lng, setLng] = useState('');
  // Optional logo (square avatar) + wide banner — uploaded to the weespas pipeline on submit.
  const [logo, setLogo] = useState<File | null>(null);
  const [banner, setBanner] = useState<File | null>(null);
  const [step, setStep] = useState('');
  const { latitude, longitude, requestLocation, loading: locLoading } = useGeolocation();
  const createShop = useCreateShop(session);
  // The claim mutation binds to a shop_id we don't know until AFTER createShop resolves; we lift
  // that call into the submit handler (via claimShopHandle from api/commerce) rather than
  // creating a mutation hook keyed by a to-be-known id. The hook stays available for the
  // dashboard's later "claim a handle" flow (chunk 3d).

  // Debounced live-probe: 300ms after the seller stops typing, hit /shops/handle-available. A
  // pre-syntax failure short-circuits the network call (fast inline red without a round-trip);
  // the probe only fires when the input passes pre-syntax so we don't burn requests on obvious
  // garbage. The trimmed+lowered form goes to the server so the response's `handle` echoes
  // whatever the seller sees.
  const debouncedHandle = useDebounce(handle.trim().toLowerCase(), 300);
  const preSyntaxReason: HandleReason | null = useMemo(
    () => (debouncedHandle ? preSyntax(debouncedHandle) : null),
    [debouncedHandle],
  );
  useEffect(() => {
    // Skip if the seller emptied the box (handle is optional) or the pre-syntax already failed.
    if (!debouncedHandle || preSyntaxReason !== null || !session) {
      setProbe(null);
      setProbing(false);
      return;
    }
    let cancelled = false;
    setProbing(true);
    checkHandleAvailable(session, debouncedHandle)
      .then((res) => {
        // Race-guard: a later keystroke may have moved on before this promise resolves; only
        // apply the answer when the debounced value still matches what we asked about.
        if (!cancelled && res.handle === debouncedHandle) {
          setProbe(res);
        }
      })
      .catch(() => {
        // Transport error — degrade to "no verdict" rather than a false positive. The submit-time
        // claim is the authority; a probe failure never blocks form submission.
        if (!cancelled) setProbe(null);
      })
      .finally(() => {
        if (!cancelled) setProbing(false);
      });
    return () => { cancelled = true; };
  }, [debouncedHandle, preSyntaxReason, session]);

  // The reason the input shows in red (from pre-syntax OR server probe). Explicitly ignores the
  // probe when the current input's debounced form no longer matches — no stale red flashes.
  const handleReason: HandleReason | null = useMemo(() => {
    if (!debouncedHandle) return null;
    if (preSyntaxReason !== null) return preSyntaxReason;
    if (probe && probe.handle === debouncedHandle && !probe.available) return probe.reason;
    return null;
  }, [debouncedHandle, preSyntaxReason, probe]);
  const handleOk = !!debouncedHandle && handleReason === null && probe?.available === true;

  // When geolocation resolves, fill the lat/lng fields (the seller can still edit them).
  useEffect(() => {
    if (latitude != null && longitude != null) {
      setLat(latitude.toFixed(6));
      setLng(longitude.toFixed(6));
    }
  }, [latitude, longitude]);

  const latN = parseFloat(lat);
  const lngN = parseFloat(lng);
  // The handle field is OPTIONAL — leaving it blank is legal (shop still gets a /shop/<sellerId>
  // URL). But if the seller HAS typed something, it must be either (a) still-mid-typing (the
  // debounced value hasn't caught up yet — safe to wait), or (b) validated. We refuse a submit
  // with a visibly-red handle: `handleReason` non-null means either pre-syntax or the server
  // probe rejected it. A submit while `probing` is true (a probe in flight, no verdict yet) is
  // allowed to fall through — the claim step is the authority, so a race there surfaces a
  // "shop created, handle failed" toast rather than freezing the form indefinitely.
  const handleFieldOk = !handle.trim() || handleReason === null;
  const valid =
    name.trim().length > 0 &&
    displayName.trim().length > 0 &&
    Number.isFinite(latN) && latN >= -90 && latN <= 90 &&
    Number.isFinite(lngN) && lngN >= -180 && lngN <= 180 &&
    handleFieldOk;

  const busy = step !== '' || createShop.isPending;

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!valid || busy) return;
    try {
      // Upload logo + banner first (if picked) through the weespas media pipeline, then create the
      // shop with the returned /uploads URLs. Each image uploads on its own so we can map the URL to
      // the right field (uploadTradeMedia returns an images[] array).
      let avatar_url: string | undefined;
      let banner_url: string | undefined;
      if (logo || banner) {
        if (!weespasToken) { toast.error('Not signed in.'); return; }
        setStep('Uploading images…');
        if (logo) {
          const up = await uploadTradeMedia(weespasToken, { images: [logo] });
          avatar_url = up.images[0]?.url;
        }
        if (banner) {
          const up = await uploadTradeMedia(weespasToken, { images: [banner] });
          banner_url = up.images[0]?.url;
        }
      }
      setStep('Creating…');
      const shop = await createShop.mutateAsync({
        name: name.trim(), display_name: displayName.trim(), lat: latN, lng: lngN,
        // Omit when unset so the server stores null rather than an empty string.
        category: category || undefined,
        avatar_url, banner_url,
      });
      // Optional handle claim — a two-step commit so shop creation is never blocked. If the claim
      // fails (a race snapped the handle between the probe and PATCH, network blip, etc.), the
      // shop still exists and we surface the specific reason so the seller can retry from the
      // dashboard. Same pattern as the media upload above: independent, ordered, best-effort on
      // the secondary step. Only fires when the handle passed pre-syntax AND the last probe
      // agreed it was available — a submit with a red input is refused earlier by `valid`.
      if (session && debouncedHandle && handleOk) {
        setStep('Claiming handle…');
        try {
          await claimShopHandle(session, shop.id, debouncedHandle);
        } catch (claimErr) {
          // The shop was created; only the handle claim failed. Toast the specific reason so the
          // seller sees why (matching the same REASON_COPY map the input uses). This is a WARNING,
          // not a fatal — createShop succeeded and the dashboard reflects it.
          const raw = claimErr instanceof Error ? claimErr.message : '';
          const known = (Object.keys(REASON_COPY) as HandleReason[]).find((r) => raw.includes(r));
          toast.error(
            'Shop created, but the handle claim failed: ' +
            (known ? REASON_COPY[known] : 'try claiming it from the dashboard.'),
          );
          onCreated?.(shop.id);
          onClose();
          return;
        }
      }
      toast.success('Shop created.');
      onCreated?.(shop.id);
      onClose();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Could not create the shop.');
    } finally {
      setStep('');
    }
  };

  return (
    <SellerModal
      title="Open a shop"
      busy={busy}
      onClose={onClose}
      footer={
        <>
          <button type="button" className="seller-btn seller-btn--ghost" disabled={busy} onClick={onClose}>Cancel</button>
          <button type="submit" form="create-shop-form" className="seller-btn seller-btn--primary" disabled={!valid || busy}>
            {step || 'Create shop'}
          </button>
        </>
      }
    >
      <form id="create-shop-form" onSubmit={submit} className="seller-form">
        <div className="seller-field">
          <label htmlFor="shop-name">Shop name</label>
          <input id="shop-name" value={name} maxLength={160} disabled={busy}
                 onChange={(e) => setName(e.target.value)} placeholder="e.g. Mama Njeri Groceries" />
        </div>
        <div className="seller-field">
          <label htmlFor="shop-display">Your display name</label>
          <input id="shop-display" value={displayName} maxLength={120} disabled={busy}
                 onChange={(e) => setDisplayName(e.target.value)} placeholder="How buyers see you" />
        </div>
        <div className="seller-field">
          <label htmlFor="shop-category">Category <span className="seller-field__hint">(optional)</span></label>
          <select id="shop-category" value={category} disabled={busy}
                  onChange={(e) => setCategory(e.target.value)} data-testid="shop-category">
            <option value="">No category</option>
            {CATEGORY_SLUGS.map((slug) => (
              <option key={slug} value={slug}>{CATEGORY_META[slug].label}</option>
            ))}
          </select>
        </div>

        <div className="seller-field">
          <label htmlFor="shop-handle">
            Shareable link <span className="seller-field__hint">(optional — pick once, permanent)</span>
          </label>
          <div className="seller-field__handle-row">
            <span className="seller-field__handle-prefix" aria-hidden="true">/shop/</span>
            <input
              id="shop-handle"
              value={handle}
              maxLength={40}
              disabled={busy}
              // Type-time normalization is DEFERRED to the debounce/server layer so the input
              // doesn't clobber the seller's cursor as they type mixed case. The `handle` state
              // holds the raw value; the debounced form (`debouncedHandle`) is what we probe with.
              onChange={(e) => setHandle(e.target.value)}
              placeholder="mama-mboga"
              inputMode="text"
              autoComplete="off"
              spellCheck={false}
              aria-invalid={handleReason !== null || undefined}
              aria-describedby={handleReason !== null ? 'shop-handle-error' : 'shop-handle-hint'}
              data-testid="shop-handle"
            />
            {/* Availability badge — visible only once we have a verdict for the current input.
                Three states: probing / available / unavailable. `handleOk` gates the ✓ so a
                stale probe never shows green. */}
            {handle.trim() !== '' && handleReason === null && (
              probing ? (
                <span className="seller-field__handle-badge seller-field__handle-badge--probing"
                      data-testid="shop-handle-badge" role="status">Checking…</span>
              ) : handleOk ? (
                <span className="seller-field__handle-badge seller-field__handle-badge--ok"
                      data-testid="shop-handle-badge" role="status">Available</span>
              ) : null
            )}
          </div>
          {handleReason !== null ? (
            <p id="shop-handle-error" className="seller-field__error" data-testid="shop-handle-error">
              {REASON_COPY[handleReason]}
            </p>
          ) : (
            <p id="shop-handle-hint" className="seller-field__hint">
              Buyers open your shop at <code>/shop/&lt;handle&gt;</code>. Leave blank to use the default URL.
            </p>
          )}
        </div>

        <div className="seller-field--row">
          <ShopImagePicker
            id="shop-logo" label="Profile picture" file={logo} onChange={setLogo} disabled={busy}
            shape="circle" testid="shop-logo"
            hint="This is your business logo — it shows on your shop and on every product you promote."
          />
          <ShopImagePicker
            id="shop-banner" label="Banner" file={banner} onChange={setBanner} disabled={busy}
            shape="wide" testid="shop-banner"
            hint="A wide cover image shown across the top of your shop profile."
          />
        </div>
        <div className="seller-field--row">
          <div className="seller-field">
            <label htmlFor="shop-lat">Latitude</label>
            <input id="shop-lat" value={lat} disabled={busy} inputMode="decimal"
                   onChange={(e) => setLat(e.target.value)} placeholder="-1.292" />
          </div>
          <div className="seller-field">
            <label htmlFor="shop-lng">Longitude</label>
            <input id="shop-lng" value={lng} disabled={busy} inputMode="decimal"
                   onChange={(e) => setLng(e.target.value)} placeholder="36.8219" />
          </div>
        </div>
        <button type="button" className="seller-btn seller-btn--ghost" disabled={busy || locLoading}
                onClick={requestLocation} data-testid="use-my-location">
          {locLoading ? 'Locating…' : 'Use my location'}
        </button>
      </form>
    </SellerModal>
  );
};

export default CreateShopForm;
