// EditListingForm — edit an existing listing (title/description/price/pricing/low-stock/video flag)
// and, from the same modal, DELETE it (soft delete). Mirrors CreateListingForm's two-token media
// flow: newly-added media uploads through the WEESPAS pipeline (weespasToken) and is APPENDED to the
// listing's existing media_urls before the commerce PATCH (COMMERCE token). Stock-on-hand is NOT
// edited here — the dashboard's inline StockControl owns live stock (absolute/delta); the field is
// hidden (showStockOnHand=false) so the two paths never fight over the same number.
import React, { useState } from 'react';
import { useToast } from '../../../context/ToastContext';
import { useUpdateListing, useDeleteListing } from '../../../hooks/useSellerMutations';
import { uploadTradeMedia, type CommerceSession, type ListingOut } from '../../../api/commerce';
import SellerModal from './SellerModal';
import TradeMediaUploader from './TradeMediaUploader';
import ProductFields, {
  listingToDraft, isProductDraftValid, productDraftToUpdate, type ProductDraft,
} from './ProductFields';

interface EditListingFormProps {
  session: CommerceSession | null;
  /** Weespas session token — used ONLY when the seller adds NEW media (the two-token exception). */
  weespasToken: string | null;
  listing: ListingOut;
  onClose: () => void;
}

const EditListingForm: React.FC<EditListingFormProps> = ({ session, weespasToken, listing, onClose }) => {
  const { toast } = useToast();
  const [draft, setDraft] = useState<ProductDraft>(() => listingToDraft(listing));
  // NEW media the seller adds during this edit (appended to the existing media_urls). Empty = keep
  // the listing's current media untouched.
  const [images, setImages] = useState<File[]>([]);
  const [video, setVideo] = useState<File | null>(null);
  const [step, setStep] = useState<string>('');
  const [confirmDelete, setConfirmDelete] = useState(false);

  const update = useUpdateListing(session, listing.id);
  const del = useDeleteListing(session, listing.id);
  const patchDraft = (patch: Partial<ProductDraft>) => setDraft((d) => ({ ...d, ...patch }));
  const isPost = listing.post_kind === 'post';
  const valid = isProductDraftValid(draft);
  const busy = step !== '' || update.isPending || del.isPending;

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!valid || busy || !session) return;
    if (draft.isShortVideo && !video && !listing.media_urls.length) {
      toast.error('A short-video post needs a video. Add one or turn the toggle off.');
      return;
    }
    try {
      const body = productDraftToUpdate(draft);
      // Only touch media_urls if the seller actually added new files — otherwise leave the existing
      // media as-is (an omitted key is untouched server-side).
      if (images.length || video) {
        if (!weespasToken) { toast.error('Not signed in.'); return; }
        setStep('Uploading media…');
        const uploaded = await uploadTradeMedia(weespasToken, { images, video });
        const added = [...uploaded.images.map((i) => i.url), ...(uploaded.video ? [uploaded.video.url] : [])];
        body.media_urls = [...listing.media_urls, ...added];
      }
      setStep('Saving…');
      await update.mutateAsync(body);
      toast.success('Listing updated.');
      onClose();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Could not save the listing.');
    } finally {
      setStep('');
    }
  };

  const doDelete = async () => {
    if (busy) return;
    try {
      setStep('Deleting…');
      await del.mutateAsync();
      toast.success('Listing removed.');
      onClose();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Could not remove the listing.');
    } finally {
      setStep('');
    }
  };

  return (
    <SellerModal
      title={isPost ? 'Edit post' : 'Edit listing'}
      busy={busy}
      onClose={onClose}
      footer={
        <>
          <button type="button" className="seller-btn seller-btn--danger" disabled={busy}
                  onClick={() => setConfirmDelete(true)} data-testid="edit-delete">
            Delete
          </button>
          <span className="seller-modal__spacer" />
          <button type="button" className="seller-btn seller-btn--ghost" disabled={busy} onClick={onClose}>Cancel</button>
          <button type="submit" form="edit-listing-form" className="seller-btn seller-btn--primary" disabled={!valid || busy}>
            {step || 'Save changes'}
          </button>
        </>
      }
    >
      {confirmDelete ? (
        <div className="seller-confirm" data-testid="delete-confirm">
          <p className="seller-confirm__msg">
            Remove <strong>{listing.title}</strong>? It disappears from the feed and your storefront.
            Past orders and receipts are kept.
          </p>
          <div className="seller-confirm__actions">
            <button type="button" className="seller-btn seller-btn--ghost" disabled={busy}
                    onClick={() => setConfirmDelete(false)}>Keep it</button>
            <button type="button" className="seller-btn seller-btn--danger" disabled={busy}
                    onClick={doDelete} data-testid="delete-confirm-yes">
              {step || 'Remove listing'}
            </button>
          </div>
        </div>
      ) : (
        <form id="edit-listing-form" onSubmit={submit} className="seller-form">
          <ProductFields
            draft={draft} onChange={patchDraft} disabled={busy} idPrefix="edit"
            showStockOnHand={false} showVideoToggle={!isPost}
          />
          <TradeMediaUploader
            images={images}
            video={video}
            onImagesChange={setImages}
            onVideoChange={setVideo}
            onError={(m) => toast.error(m)}
            disabled={busy}
          />
          {listing.media_urls.length > 0 && (
            <p className="seller-field__hint seller-field__hint--block">
              Adding media appends to the {listing.media_urls.length} item{listing.media_urls.length === 1 ? '' : 's'} already on this listing.
            </p>
          )}
        </form>
      )}
    </SellerModal>
  );
};

export default EditListingForm;
