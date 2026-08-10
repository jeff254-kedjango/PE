// ComposerBox — the inline "Write something…" timeline composer (§8). It is the TOP of the feed
// column, with the lane toggle seated directly beneath it as one card (see TradePage).
//
// Two publish modes:
//   * Post    — plain social content (text + optional media). Published to the caller's personal
//               timeline shop (auto-provisioned server-side); needs no shop.
//   * Product — a sellable listing. Requires the caller to already have a shop (the "only shop
//               owners post products" gate); if they have none, a CTA points to the seller console.
//
// The ACTION ROW under the prompt (Write Post / Sell Product / Post a Video / …) is the ENTRY point:
// it expands the composer already in the right mode, and for the media actions it opens the file
// dialog in the same gesture. The Post|Product segmented control above the fields is the mode's
// INDICATOR once expanded. Both are views of the same `mode` state — neither owns a private copy, so
// they cannot drift.
//
// Media uploads use the WEESPAS token (uploadTradeMedia, the two-token exception); the create
// calls use the COMMERCE session. Emoji insert at the caret via the shared EmojiPalette.
import React, { useRef, useState } from 'react';
import { flushSync } from 'react-dom';
import { Link } from 'react-router-dom';
import { useToast } from '../../context/ToastContext';
import { useMyStorefront } from '../../hooks/useMyStorefront';
import { useCreatePost, useCreateListing } from '../../hooks/useSellerMutations';
import { uploadTradeMedia, type CommerceSession, type StorefrontShop } from '../../api/commerce';
import { insertAtCursor } from '../../utils/insertAtCursor';
import Icon from '../ui/Icon';
import EmojiPalette from './EmojiPalette';
import TradeMediaUploader, { type TradeMediaUploaderHandle } from './seller/TradeMediaUploader';
import ProductFields, {
  emptyProductDraft, isProductDraftValid, productDraftToListing, type ProductDraft,
} from './seller/ProductFields';
import './seller/sellerForm.css'; // seller-btn / seller-field used directly below (actions + shop select)
import './ComposerBox.css';

type Mode = 'post' | 'product';

// ── Composer action row ─────────────────────────────────────────────────────────────────────────
// The six affordances under the prompt. Declared as data (not six hand-written buttons) so the row
// order, the a11y names and the disabled set can't drift apart across edits.
//
// `action` is what the click DOES, and it is exhaustive over a closed union — adding a seventh entry
// without handling it is a compile error, not a silently inert button (rule 4: no dead code).
//   'post'    → expand in Post mode.
//   'product' → expand in Product mode.
//   'images'  → expand in Post mode AND open the image picker in the same gesture.
//   'video'   → expand in Post mode AND open the video picker in the same gesture.
//   'soon'    → NO backend exists for this yet, so the button says so instead of pretending: commerce
//               has no poll model, and weespas/routers/media.py's upload allowlist is images+video
//               only (no audio MIME is accepted). We show them because they're on the roadmap, but a
//               button that silently does nothing would be worse than one that admits it isn't
//               built. Dimmed and labelled "— coming soon", but a REAL enabled button — see the
//               render for why neither `disabled` nor `aria-disabled` belongs on it.
type ComposerAction = 'post' | 'product' | 'images' | 'video' | 'soon';

interface ComposerActionSpec {
  key: string;
  label: string;
  icon: 'edit' | 'money' | 'play' | 'poll' | 'image' | 'mic';
  action: ComposerAction;
}

const COMPOSER_ACTIONS: readonly ComposerActionSpec[] = [
  { key: 'write', label: 'Write Post', icon: 'edit', action: 'post' },
  { key: 'sell', label: 'Sell Product', icon: 'money', action: 'product' },
  { key: 'video', label: 'Post a Video', icon: 'play', action: 'video' },
  { key: 'poll', label: 'Create Poll', icon: 'poll', action: 'soon' },
  { key: 'pictures', label: 'Post Pictures', icon: 'image', action: 'images' },
  { key: 'audio', label: 'Post Audio', icon: 'mic', action: 'soon' },
] as const;

interface ComposerBoxProps {
  session: CommerceSession;
  /** Weespas token — used ONLY for media upload (the two-token exception). */
  weespasToken: string | null;
  /** Buyer's current location — anchors a plain post in the proximity feed. */
  lat: number;
  lng: number;
  /** Display name for the post's author snapshot (falls back to the token's name claim server-side). */
  authorName?: string | null;
}

const ComposerBox: React.FC<ComposerBoxProps> = ({ session, weespasToken, lat, lng, authorName }) => {
  const { toast } = useToast();
  const [expanded, setExpanded] = useState(false);
  const [mode, setMode] = useState<Mode>('post');

  // Post-mode draft.
  const [body, setBody] = useState('');
  const bodyRef = useRef<HTMLTextAreaElement>(null);
  const emojiBtnRef = useRef<HTMLButtonElement>(null);
  const [showEmoji, setShowEmoji] = useState(false);

  // Product-mode draft (shared field set).
  const [draft, setDraft] = useState<ProductDraft>(emptyProductDraft);
  const patchDraft = (patch: Partial<ProductDraft>) => setDraft((d) => ({ ...d, ...patch }));

  // Shared media + submit state.
  const [images, setImages] = useState<File[]>([]);
  const [video, setVideo] = useState<File | null>(null);
  const [step, setStep] = useState('');
  const uploaderRef = useRef<TradeMediaUploaderHandle>(null);

  const storefront = useMyStorefront(session);
  const shops: StorefrontShop[] = storefront.data?.shops ?? [];
  const [shopId, setShopId] = useState('');
  const effectiveShopId = shopId || shops[0]?.shop.id || '';

  const createPost = useCreatePost(session);
  const createListing = useCreateListing(session, effectiveShopId);
  const busy = step !== '' || createPost.isPending || createListing.isPending;

  const postValid = body.trim().length > 0 || images.length > 0 || !!video;
  const productValid = !!effectiveShopId && isProductDraftValid(draft);
  const canSubmit = mode === 'post' ? postValid : productValid;

  const insertEmoji = (emoji: string) => {
    const el = bodyRef.current;
    const { next, caret } = insertAtCursor(el, body, emoji);
    setBody(next);
    setShowEmoji(false);
    // Restore focus + caret after React re-renders the controlled value.
    requestAnimationFrame(() => {
      if (el) { el.focus(); el.setSelectionRange(caret, caret); }
    });
  };

  const reset = () => {
    setBody(''); setDraft(emptyProductDraft); setImages([]); setVideo(null);
    setShowEmoji(false); setExpanded(false);
  };

  // Action-row click. Sets the publish mode and, for the two media actions, lands the user straight
  // on the file dialog.
  //
  // Why flushSync: a browser only opens a file dialog from a TRUSTED user gesture, and the <input>
  // we must .click() lives inside TradeMediaUploader, which isn't mounted while the composer is
  // collapsed. A normal setState would mount it after this handler returns — by then the gesture has
  // expired and the dialog is suppressed. flushSync commits the expansion synchronously, inside the
  // gesture, so the input exists and the click is still trusted. This is the documented escape hatch
  // for exactly this case; it is scoped to one click, not a render-path cost.
  const runAction = (action: ComposerAction) => {
    if (busy) return;
    switch (action) {
      case 'soon':
        // Honest: the feature is announced but not built. See COMPOSER_ACTIONS.
        toast.info('Not available yet — coming soon.');
        return;
      case 'post':
        setExpanded(true); setMode('post');
        return;
      case 'product':
        setExpanded(true); setMode('product');
        return;
      case 'images':
      case 'video':
        // Media attaches to a plain post; Product mode has its own console flow.
        flushSync(() => { setExpanded(true); setMode('post'); });
        if (action === 'images') uploaderRef.current?.pickImages();
        else uploaderRef.current?.pickVideo();
        return;
    }
  };

  // Attaching a video in PRODUCT mode auto-marks it a short video (#1: lands in the Videos lane
  // without remembering the toggle; still opt-out via the toggle). Post mode derives is_short_video
  // at submit, so only the draft flag needs syncing here.
  const onVideoChange = (file: File | null) => {
    setVideo(file);
    if (mode === 'product') patchDraft({ isShortVideo: !!file });
  };

  const uploadMedia = async (): Promise<string[]> => {
    if (!images.length && !video) return [];
    if (!weespasToken) { throw new Error('Not signed in.'); }
    setStep('Uploading media…');
    const uploaded = await uploadTradeMedia(weespasToken, { images, video });
    return [...uploaded.images.map((i) => i.url), ...(uploaded.video ? [uploaded.video.url] : [])];
  };

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!canSubmit || busy) return;
    // A short-video product should actually carry a video (§8) — same guard as the console.
    if (mode === 'product' && draft.isShortVideo && !video) {
      toast.error('A short-video post needs a video. Add one or turn the toggle off.');
      return;
    }
    try {
      const mediaUrls = await uploadMedia();
      setStep(mode === 'post' ? 'Posting…' : 'Publishing…');
      if (mode === 'post') {
        await createPost.mutateAsync({
          body: body.trim(),
          media_urls: mediaUrls,
          is_short_video: !!video && images.length === 0,
          lat, lng,
          author_name: authorName ?? null,
        });
        toast.success('Posted to your timeline.');
      } else {
        await createListing.mutateAsync(productDraftToListing(draft, mediaUrls));
        toast.success('Product published.');
      }
      reset();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Could not post. Try again.');
    } finally {
      setStep('');
    }
  };

  // The action row. Rendered under the prompt in BOTH states from one definition — collapsed it's
  // the way in, expanded it's the way to switch mode / attach media without collapsing first.
  const actionRow = (
    <div className="composer__tools" role="group" aria-label="Post something">
      {COMPOSER_ACTIONS.map((spec) => {
        const soon = spec.action === 'soon';
        return (
          <button
            key={spec.key}
            type="button"
            className={`composer__tool${soon ? ' composer__tool--soon' : ''}`}
            // `soon` buttons are genuinely ENABLED — and deliberately NOT aria-disabled. They do
            // something real: they tell you the feature isn't built yet. Marking them disabled (either
            // natively or via aria) would claim "this does nothing", which is both a lie and a dead
            // end for keyboard/screen-reader users, who'd get silence instead of the explanation.
            // The unavailability is carried by the visible LABEL instead (see aria-label), so AT
            // announces "Create Poll — coming soon" and the state is spoken, not merely implied.
            // Only `busy` disables anything here, and it disables the whole row uniformly.
            disabled={busy}
            onClick={() => runAction(spec.action)}
            aria-label={soon ? `${spec.label} — coming soon` : undefined}
            title={soon ? `${spec.label} — coming soon` : spec.label}
            data-testid={`composer-tool-${spec.key}`}
          >
            <Icon name={spec.icon} size={18} />
            <span className="composer__tool-label">{spec.label}</span>
          </button>
        );
      })}
    </div>
  );

  if (!expanded) {
    return (
      <div className="composer composer--collapsed">
        <button
          type="button"
          className="composer__prompt"
          onClick={() => setExpanded(true)}
          data-testid="composer-open"
        >
          Write something…
        </button>
        {actionRow}
      </div>
    );
  }

  const hasShop = shops.length > 0;

  return (
    <form className="composer" onSubmit={submit} data-testid="composer">
      <div className="composer__modes" role="tablist" aria-label="Post type">
        <button
          type="button" role="tab" aria-selected={mode === 'post'}
          className={`composer__mode${mode === 'post' ? ' composer__mode--on' : ''}`}
          onClick={() => setMode('post')} data-testid="composer-mode-post"
        >
          Post
        </button>
        <button
          type="button" role="tab" aria-selected={mode === 'product'}
          className={`composer__mode${mode === 'product' ? ' composer__mode--on' : ''}`}
          onClick={() => setMode('product')} data-testid="composer-mode-product"
        >
          Product
        </button>
      </div>

      {mode === 'post' ? (
        <div className="composer__post">
          <div className="composer__body-wrap">
            <textarea
              ref={bodyRef}
              className="composer__body"
              value={body}
              rows={3}
              maxLength={2000}
              disabled={busy}
              placeholder="Share an update, ask the neighbourhood, or show what you've got…"
              onChange={(e) => setBody(e.target.value)}
              data-testid="composer-body"
            />
            {showEmoji && (
              <EmojiPalette onPick={insertEmoji} onClose={() => setShowEmoji(false)} anchorRef={emojiBtnRef} />
            )}
          </div>
          <button
            ref={emojiBtnRef}
            type="button"
            className="composer__emoji-btn"
            onClick={() => setShowEmoji((s) => !s)}
            disabled={busy}
            aria-label="Add emoji"
            data-testid="composer-emoji"
          >
            😊
          </button>
        </div>
      ) : !hasShop ? (
        <div className="composer__no-shop" data-testid="composer-no-shop">
          <p>Products are sold from a shop. Open yours to list items here.</p>
          <Link to="/trade/sell" className="composer__shop-cta">Open your shop</Link>
        </div>
      ) : (
        <div className="composer__product seller-form">
          {shops.length > 1 && (
            <div className="seller-field">
              <label htmlFor="composer-shop">Shop</label>
              <select
                id="composer-shop" value={effectiveShopId} disabled={busy}
                onChange={(e) => setShopId(e.target.value)}
              >
                {shops.map((s) => <option key={s.shop.id} value={s.shop.id}>{s.shop.name}</option>)}
              </select>
            </div>
          )}
          <ProductFields draft={draft} onChange={patchDraft} disabled={busy} idPrefix="composer" />
        </div>
      )}

      {actionRow}

      {(mode === 'post' || hasShop) && (
        <TradeMediaUploader
          ref={uploaderRef}
          images={images}
          video={video}
          onImagesChange={setImages}
          onVideoChange={onVideoChange}
          onError={(m) => toast.error(m)}
          disabled={busy}
        />
      )}

      <div className="composer__actions">
        <button type="button" className="seller-btn seller-btn--ghost" disabled={busy} onClick={reset}>
          Cancel
        </button>
        <button
          type="submit"
          className="seller-btn seller-btn--primary"
          disabled={!canSubmit || busy}
          data-testid="composer-submit"
        >
          {step || (mode === 'post' ? 'Post' : 'Publish')}
        </button>
      </div>
    </form>
  );
};

export default ComposerBox;
