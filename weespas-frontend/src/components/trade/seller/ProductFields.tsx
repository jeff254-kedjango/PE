// ProductFields — the controlled product field set shared by the seller-console modal
// (CreateListingForm) and the inline timeline composer (ComposerBox, Product mode). Owning the
// markup + the draft→ListingCreate mapping in ONE place keeps the two surfaces from drifting (no
// duplicated form, rule: no dead/duplicated code). Purely presentational: the parent owns the
// draft state and the submit; this renders inputs + emits patches.
import React from 'react';
import {
  majorToCents, centsToMajor, DESCRIPTION_MAX_LEN,
  type ListingCreate, type ListingOut, type ListingUpdate, type PricingMode,
} from '../../../api/commerce';
import './sellerForm.css';

export interface ProductDraft {
  title: string;
  description: string;
  price: string;        // major units as typed; converted to integer cents on submit
  pricingMode: PricingMode;
  stock: string;
  lowStock: string;
  isShortVideo: boolean;
}

export const emptyProductDraft: ProductDraft = {
  title: '', description: '', price: '', pricingMode: 'fixed',
  stock: '1', lowStock: '0', isShortVideo: false,
};

/** True when the draft is a publishable product (title + non-negative integer money/stock). */
export function isProductDraftValid(d: ProductDraft): boolean {
  const cents = majorToCents(d.price);
  const stockN = parseInt(d.stock, 10);
  const lowN = parseInt(d.lowStock, 10);
  return (
    d.title.trim().length > 0 &&
    cents != null &&
    Number.isInteger(stockN) && stockN >= 0 &&
    Number.isInteger(lowN) && lowN >= 0
  );
}

/** Map a (valid) draft + uploaded media URLs to the commerce ListingCreate body. Assumes
 *  isProductDraftValid(d) — call it first; price_cents is asserted non-null here. */
export function productDraftToListing(d: ProductDraft, mediaUrls: string[]): ListingCreate {
  return {
    title: d.title.trim(),
    description: d.description.trim() || null,
    price_cents: majorToCents(d.price)!,
    pricing_mode: d.pricingMode,
    stock_qty: parseInt(d.stock, 10),
    low_stock_threshold: parseInt(d.lowStock, 10),
    is_short_video: d.isShortVideo,
    media_urls: mediaUrls,
  };
}

/** Seed a draft from an existing listing (for the edit form). Stock-on-hand is deliberately left
 *  blank — it isn't edited through the product form (the inline POS StockControl owns it), so the
 *  edit form hides that field (showStockOnHand=false). */
export function listingToDraft(li: ListingOut): ProductDraft {
  return {
    title: li.title,
    description: li.description ?? '',
    price: centsToMajor(li.price_cents),
    pricingMode: li.pricing_mode,
    stock: String(li.stock_qty),
    lowStock: String(li.low_stock_threshold),
    isShortVideo: li.is_short_video,
  };
}

/** Map a (valid) draft to a ListingUpdate PATCH body for an EDIT. Omits stock_qty (POS-only) and
 *  media_urls (the caller merges media separately, only when it changed). Assumes
 *  isProductDraftValid(d). */
export function productDraftToUpdate(d: ProductDraft): ListingUpdate {
  return {
    title: d.title.trim(),
    description: d.description.trim() || null,
    price_cents: majorToCents(d.price)!,
    pricing_mode: d.pricingMode,
    low_stock_threshold: parseInt(d.lowStock, 10),
    is_short_video: d.isShortVideo,
  };
}

interface ProductFieldsProps {
  draft: ProductDraft;
  onChange: (patch: Partial<ProductDraft>) => void;
  disabled?: boolean;
  /** Distinct DOM-id prefix so the modal and composer can both mount without id collisions. */
  idPrefix?: string;
  /** Whether to render the "post as short video" toggle (the composer shows it; can be hidden). */
  showVideoToggle?: boolean;
  /** Whether to render the "stock on hand" input. The EDIT form hides it — live stock is changed
   *  through the inline POS StockControl (absolute/delta), not re-typed here. */
  showStockOnHand?: boolean;
}

const ProductFields: React.FC<ProductFieldsProps> = ({
  draft, onChange, disabled, idPrefix = 'li', showVideoToggle = true, showStockOnHand = true,
}) => (
  <>
    <div className="seller-field">
      <label htmlFor={`${idPrefix}-title`}>Title</label>
      <input
        id={`${idPrefix}-title`} value={draft.title} maxLength={200} disabled={disabled}
        onChange={(e) => onChange({ title: e.target.value })}
        placeholder="e.g. Fresh sukuma, per bunch"
      />
    </div>
    <div className="seller-field">
      <label htmlFor={`${idPrefix}-desc`}>Description <span className="seller-field__hint">(optional)</span></label>
      <textarea
        id={`${idPrefix}-desc`} className="seller-field__textarea" value={draft.description} rows={4}
        maxLength={DESCRIPTION_MAX_LEN} disabled={disabled} data-testid="listing-description"
        onChange={(e) => onChange({ description: e.target.value })}
        placeholder={'Tell buyers about it — condition, sizes, delivery…\n\nPress Enter twice for a new paragraph.'}
      />
      <span className="seller-field__counter">{draft.description.length}/{DESCRIPTION_MAX_LEN}</span>
    </div>
    <div className="seller-field--row">
      <div className="seller-field">
        <label htmlFor={`${idPrefix}-price`}>Price (KES)</label>
        <input
          id={`${idPrefix}-price`} value={draft.price} disabled={disabled} inputMode="decimal"
          onChange={(e) => onChange({ price: e.target.value })} placeholder="150"
        />
      </div>
      <div className="seller-field">
        <label htmlFor={`${idPrefix}-mode`}>Pricing</label>
        <select
          id={`${idPrefix}-mode`} value={draft.pricingMode} disabled={disabled}
          onChange={(e) => onChange({ pricingMode: e.target.value as PricingMode })}
        >
          <option value="fixed">Fixed price</option>
          <option value="bargain">Open to bargain</option>
        </select>
      </div>
    </div>
    <div className="seller-field--row">
      {showStockOnHand && (
        <div className="seller-field">
          <label htmlFor={`${idPrefix}-stock`}>Stock on hand</label>
          <input
            id={`${idPrefix}-stock`} value={draft.stock} disabled={disabled} inputMode="numeric"
            onChange={(e) => onChange({ stock: e.target.value })}
          />
        </div>
      )}
      <div className="seller-field">
        <label htmlFor={`${idPrefix}-low`}>Low-stock alert at</label>
        <input
          id={`${idPrefix}-low`} value={draft.lowStock} disabled={disabled} inputMode="numeric"
          onChange={(e) => onChange({ lowStock: e.target.value })}
        />
      </div>
    </div>
    {showVideoToggle && (
      <label className="seller-toggle">
        <input
          type="checkbox" checked={draft.isShortVideo} disabled={disabled}
          onChange={(e) => onChange({ isShortVideo: e.target.checked })} data-testid="short-video-toggle"
        />
        Post as a short video (appears under the Videos tab)
      </label>
    )}
  </>
);

export default ProductFields;
