// CreateListingForm — publish a listing (a sale IS a post, §8). Two-step submit:
//   1. uploadTradeMedia(weespasToken, {images, video}) → /uploads URLs   (WEESPAS token!)
//   2. createListing(session, shopId, {…media_urls, is_short_video, …})  (COMMERCE token)
// The two-token split is deliberate (media lives in the weespas pipeline; trade lives in commerce);
// see api/commerce.ts uploadTradeMedia. Price is entered in MAJOR units and converted to integer
// cents (majorToCents) — commerce stores integer money only (S9).
import React, { useState } from 'react';
import { useToast } from '../../../context/ToastContext';
import { useCreateListing } from '../../../hooks/useSellerMutations';
import {
  uploadTradeMedia,
  type CommerceSession, type StorefrontShop,
} from '../../../api/commerce';
import SellerModal from './SellerModal';
import TradeMediaUploader from './TradeMediaUploader';
import ProductFields, {
  emptyProductDraft, isProductDraftValid, productDraftToListing, type ProductDraft,
} from './ProductFields';

interface CreateListingFormProps {
  session: CommerceSession | null;
  /** Weespas session token — used ONLY for the media upload (the two-token exception). */
  weespasToken: string | null;
  /** The seller's shops (to pick which one the listing belongs to). */
  shops: StorefrontShop[];
  /** Preselected shop (e.g. opened from a shop's "Add listing"). */
  defaultShopId?: string;
  onClose: () => void;
}

const CreateListingForm: React.FC<CreateListingFormProps> = ({
  session, weespasToken, shops, defaultShopId, onClose,
}) => {
  const { toast } = useToast();
  const [shopId, setShopId] = useState(defaultShopId ?? shops[0]?.shop.id ?? '');
  const [draft, setDraft] = useState<ProductDraft>(emptyProductDraft);
  const [images, setImages] = useState<File[]>([]);
  const [video, setVideo] = useState<File | null>(null);
  const [step, setStep] = useState<string>('');

  const createListing = useCreateListing(session, shopId);
  const patchDraft = (patch: Partial<ProductDraft>) => setDraft((d) => ({ ...d, ...patch }));
  const valid = !!shopId && isProductDraftValid(draft);
  const busy = step !== '' || createListing.isPending;

  // Attaching a video auto-marks the post as a short video (so it lands in the Videos lane without
  // the seller having to remember the toggle — #1). Removing the video un-marks it. The toggle is
  // still shown so a seller can opt out (keep a clip as ordinary listing media).
  const onVideoChange = (file: File | null) => {
    setVideo(file);
    patchDraft({ isShortVideo: !!file });
  };

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!valid || busy || !session) return;
    // A "short video" post should actually carry a video — guide the user (§8) rather than ship an empty kind.
    if (draft.isShortVideo && !video) {
      toast.error('A short-video post needs a video. Add one or turn the toggle off.');
      return;
    }
    try {
      let mediaUrls: string[] = [];
      if (images.length || video) {
        if (!weespasToken) { toast.error('Not signed in.'); return; }
        setStep('Uploading media…');
        const uploaded = await uploadTradeMedia(weespasToken, { images, video });
        mediaUrls = [...uploaded.images.map((i) => i.url), ...(uploaded.video ? [uploaded.video.url] : [])];
      }
      setStep('Publishing…');
      await createListing.mutateAsync(productDraftToListing(draft, mediaUrls));
      toast.success('Listing published.');
      onClose();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Could not publish the listing.');
    } finally {
      setStep('');
    }
  };

  return (
    <SellerModal
      title="New listing"
      busy={busy}
      onClose={onClose}
      footer={
        <>
          <button type="button" className="seller-btn seller-btn--ghost" disabled={busy} onClick={onClose}>Cancel</button>
          <button type="submit" form="create-listing-form" className="seller-btn seller-btn--primary" disabled={!valid || busy}>
            {step || 'Publish'}
          </button>
        </>
      }
    >
      <form id="create-listing-form" onSubmit={submit} className="seller-form">
        {shops.length > 1 && (
          <div className="seller-field">
            <label htmlFor="li-shop">Shop</label>
            <select id="li-shop" value={shopId} disabled={busy} onChange={(e) => setShopId(e.target.value)}>
              {shops.map((s) => <option key={s.shop.id} value={s.shop.id}>{s.shop.name}</option>)}
            </select>
          </div>
        )}

        <ProductFields draft={draft} onChange={patchDraft} disabled={busy} idPrefix="li" />

        <TradeMediaUploader
          images={images}
          video={video}
          onImagesChange={setImages}
          onVideoChange={onVideoChange}
          onError={(m) => toast.error(m)}
          disabled={busy}
        />
      </form>
    </SellerModal>
  );
};

export default CreateListingForm;
