// SellerDashboard — the console's home: the seller's shops and every listing (in- AND out-of-stock),
// with price, stock state, promo state, and inline POS (StockControl). Traditional admin layout —
// NOT the social feed look (the locked "shops are catalogue / feed is social" split).
import React from 'react';
import { resolveMediaUrl } from '../../../utils/media';
import { useMyStorefront } from '../../../hooks/useMyStorefront';
import { formatPrice, type CommerceSession, type ListingOut, type ShopOut } from '../../../api/commerce';
import BulkStockUpload from './BulkStockUpload';
import StockControl from './StockControl';
import './SellerDashboard.css';

interface SellerDashboardProps {
  session: CommerceSession | null;
  onCreateShop: () => void;
  onCreateListing: (shopId: string) => void;
  /** Open the "selling now" promotion chooser for a listing (FE-2b). */
  onPromote: (listing: ListingOut) => void;
  /** Open the Boost (reach) chooser for a listing (FE-2b). */
  onBoost: (listing: ListingOut) => void;
  /** Open the Flash Sale chooser for a listing (§8). */
  onFlash: (listing: ListingOut) => void;
  /** Open the edit/delete modal for a listing. */
  onEdit: (listing: ListingOut) => void;
  /** Open the per-shop sponsored-cap request modal (§8.3 item 1). */
  onManageCap: (shop: ShopOut) => void;
}

function stockBadge(li: ListingOut): { label: string; cls: string } {
  if (li.is_out_of_stock) return { label: 'Out of stock', cls: 'is-out' };
  if (li.is_low_stock) return { label: `Low · ${li.stock_qty}`, cls: 'is-low' };
  return { label: `${li.stock_qty} in stock`, cls: 'is-ok' };
}

const SellerDashboard: React.FC<SellerDashboardProps> = ({ session, onCreateShop, onCreateListing, onPromote, onBoost, onFlash, onEdit, onManageCap }) => {
  const { data, isLoading, isError, error } = useMyStorefront(session);

  if (isLoading) return <div className="seller-dash__state" role="status">Loading your shops…</div>;
  if (isError) {
    return <div className="seller-dash__state seller-dash__state--error" role="alert">
      Couldn’t load your console. {error?.message ?? ''}
    </div>;
  }

  const shops = data?.shops ?? [];

  if (shops.length === 0) {
    return (
      <div className="seller-dash__empty" role="status">
        <h2>Turn your place into a shop</h2>
        <p>Open a shop, then list what you’re selling — your neighbours discover it instantly.</p>
        <button type="button" className="seller-btn seller-btn--primary" onClick={onCreateShop}>Open a shop</button>
      </div>
    );
  }

  return (
    <div className="seller-dash">
      <div className="seller-dash__header">
        {data?.rating != null && (
          <span className="seller-dash__rating" title={`${data.review_count} review${data.review_count === 1 ? '' : 's'}`}>
            ★ {data.rating.toFixed(1)} <em>({data.review_count})</em>
          </span>
        )}
        <button type="button" className="seller-btn seller-btn--ghost" onClick={onCreateShop}>+ New shop</button>
      </div>

      {/* Chunk E3: bulk-CSV stock updater. Compact strip above the shop list; the E1 per-row
          stepper stays the primary path for small changes, this handles the "monthly restock
          from POS" case. */}
      <BulkStockUpload session={session} />

      {shops.map(({ shop, listings }) => (
        <section key={shop.id} className="seller-dash__shop">
          <div className="seller-dash__shop-head">
            <h3>{shop.name}</h3>
            <div className="seller-dash__shop-actions">
              <button type="button" className="seller-btn seller-btn--ghost" onClick={() => onManageCap(shop)}
                      data-testid="dash-manage-cap">
                Sponsored cap
              </button>
              <button type="button" className="seller-btn seller-btn--primary" onClick={() => onCreateListing(shop.id)}>
                + Add listing
              </button>
            </div>
          </div>

          {listings.length === 0 ? (
            <p className="seller-dash__shop-empty">No listings yet.</p>
          ) : (
            <ul className="seller-dash__listings">
              {listings.map((li) => {
                const badge = stockBadge(li);
                const thumb = resolveMediaUrl(li.media_urls[0]);
                return (
                  <li key={li.id} className="seller-dash__row" data-testid="dash-listing">
                    <div className="seller-dash__thumb">
                      {thumb ? <img src={thumb} alt={li.title} loading="lazy" />
                             : <span aria-hidden="true">{li.title.slice(0, 1)}</span>}
                      {li.is_short_video && <span className="seller-dash__vbadge">▶</span>}
                    </div>
                    <div className="seller-dash__meta">
                      <span className="seller-dash__title" title={li.title}>{li.title}</span>
                      <span className="seller-dash__sub">
                        {formatPrice(li.price_cents, li.currency)} · {li.pricing_mode}
                        {li.is_promoted && <em className="seller-dash__promo"> · Selling now</em>}
                      </span>
                      <span className={`seller-dash__stock seller-dash__stock--${badge.cls}`}>{badge.label}</span>
                    </div>
                    <div className="seller-dash__row-actions">
                      <StockControl session={session} listingId={li.id} stockQty={li.stock_qty} />
                      <div className="seller-dash__reach">
                        <button type="button" className="seller-dash__reach-btn" onClick={() => onEdit(li)}
                                data-testid="dash-edit">
                          Edit
                        </button>
                        <button type="button" className="seller-dash__reach-btn" onClick={() => onPromote(li)}
                                data-testid="dash-promote">
                          {li.is_promoted ? 'Promoted' : 'Promote'}
                        </button>
                        <button type="button" className="seller-dash__reach-btn" onClick={() => onBoost(li)}
                                data-testid="dash-boost">
                          Boost
                        </button>
                        {/* Flash sales are a one-tap "buy now" offer, so only fixed-price listings
                            (not bargain ones) can run one — mirrors the server's 422 guard. */}
                        {li.pricing_mode === 'fixed' && (
                          <button type="button" className="seller-dash__reach-btn" onClick={() => onFlash(li)}
                                  data-testid="dash-flash">
                            {li.is_flash_active ? 'Flash live' : 'Flash sale'}
                          </button>
                        )}
                      </div>
                    </div>
                  </li>
                );
              })}
            </ul>
          )}
        </section>
      ))}
    </div>
  );
};

export default SellerDashboard;
