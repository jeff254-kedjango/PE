// SellerConsolePage — the seller's home for the commerce trading layer (§8 / §9).
//
// Gated on isAuthenticated ALONE — no role check. "Every house a shop" (§9): the commerce bridge
// already grants create:trades to every authenticated weespas user, so anyone signed in can sell.
// (Contrast property creation, which requires an agent role.)
//
// The page holds BOTH tokens and routes them correctly:
//   * the WEESPAS session token (useAuth) → ONLY the media upload (weespas /uploads pipeline);
//   * the COMMERCE session token (useCommerceSession) → every shop/listing/POS call.
// This two-token split is the feature's main integration subtlety (see api/commerce.ts).
import React, { useState } from 'react';
import { Link } from 'react-router-dom';
import { useAuth } from '../context/AuthContext';
import { useCommerceSession } from '../hooks/useCommerceSession';
import { useMyStorefront } from '../hooks/useMyStorefront';
import SellerDashboard from '../components/trade/seller/SellerDashboard';
import CreateShopForm from '../components/trade/seller/CreateShopForm';
import CreateListingForm from '../components/trade/seller/CreateListingForm';
import EditListingForm from '../components/trade/seller/EditListingForm';
import PromoteChooser from '../components/trade/seller/PromoteChooser';
import BoostChooser from '../components/trade/seller/BoostChooser';
import FlashSaleChooser from '../components/trade/seller/FlashSaleChooser';
import SponsoredCapChooser from '../components/trade/seller/SponsoredCapChooser';
import InquiriesCard from '../components/trade/seller/InquiriesCard';
import LowStockCard from '../components/trade/seller/LowStockCard';
import RankingCard from '../components/trade/seller/RankingCard';
import ViewingCard from '../components/trade/seller/ViewingCard';
import PageMeta from '../components/ui/PageMeta';
import type { ListingOut, ShopOut } from '../api/commerce';
import './SellerConsolePage.css';

const SellerConsolePage: React.FC = () => {
  const { isAuthenticated, token } = useAuth();
  const { session, isLoading: sessionLoading, error: sessionError } = useCommerceSession();
  // The dashboard reads this too; we read it here only to feed the listing form's shop picker.
  const { data: storefront } = useMyStorefront(session);

  const [showCreateShop, setShowCreateShop] = useState(false);
  const [listingShopId, setListingShopId] = useState<string | null>(null);
  const [promoteListing, setPromoteListing] = useState<ListingOut | null>(null);
  const [boostListing, setBoostListing] = useState<ListingOut | null>(null);
  const [flashListing, setFlashListing] = useState<ListingOut | null>(null);
  const [editListing, setEditListing] = useState<ListingOut | null>(null);
  const [capShop, setCapShop] = useState<ShopOut | null>(null);

  if (!isAuthenticated) {
    return (
      <div className="seller-console seller-console--gate">
        <PageMeta title="Sell on Weespas" description="Open a shop and sell to neighbours near you." />
        <h1>Sell on Weespas</h1>
        <p>Sign in to open your shop and start selling.</p>
        <Link to="/login?next=trade/sell" className="seller-btn seller-btn--primary">Sign in</Link>
      </div>
    );
  }

  const shops = storefront?.shops ?? [];

  return (
    <div className="seller-console">
      <PageMeta title="Sell on Weespas" description="Open a shop and sell to neighbours near you." />
      <header className="seller-console__header">
        <div>
          <p className="eyebrow">Your shop</p>
          <h1>Sell on Weespas</h1>
        </div>
        <Link to="/trade" className="seller-btn seller-btn--ghost">View feed</Link>
      </header>

      {sessionLoading && <p className="seller-console__state">Connecting…</p>}
      {sessionError && (
        <p className="seller-console__state seller-console__state--error" role="alert">
          Couldn’t start a seller session. {sessionError.message}
        </p>
      )}

      {/* Two-column layout (§8 Chunks A–D): LEFT column is the seller's intelligence surface —
          Ranking → Viewing → Inquiries, each in its own card. RIGHT column is SellerDashboard
          (inventory + POS). Cards share outer chrome (padding + border + radius) so the column
          reads as a stack. */}
      {session && (
        <div className="seller-console__columns" data-testid="seller-console-columns">
          <div className="seller-console__col seller-console__col--left">
            <RankingCard session={session} />
            {/* ViewingCard is scoped to the caller's PRIMARY shop (index 0). Most sellers have
                one shop; when a "which shop?" chooser lands, this becomes a bound param. */}
            <ViewingCard session={session} shop={shops[0]?.shop ?? null} />
            {/* Chunk D: Inquiries card — a thin wrapper around the existing InquiriesInbox
                (same query cache, same mark-read UX, same list rendering). Card chrome + unread
                counter beside the header mirrors the ViewingCard's (N) pattern. */}
            <InquiriesCard session={session} />
            {/* Chunk E2: Low-stock triage. Clicking Restock reuses the same EditListingForm
                the dashboard row's Edit button opens — one editor, two entry points. */}
            <LowStockCard session={session} onRestock={(li) => setEditListing(li)} />
          </div>
          <div className="seller-console__col seller-console__col--right">
            <SellerDashboard
              session={session}
              onCreateShop={() => setShowCreateShop(true)}
              onCreateListing={(shopId) => setListingShopId(shopId)}
              onPromote={(listing) => setPromoteListing(listing)}
              onBoost={(listing) => setBoostListing(listing)}
              onFlash={(listing) => setFlashListing(listing)}
              onEdit={(listing) => setEditListing(listing)}
              onManageCap={(shop) => setCapShop(shop)}
            />
          </div>
        </div>
      )}

      {showCreateShop && (
        <CreateShopForm
          session={session}
          weespasToken={token}
          onClose={() => setShowCreateShop(false)}
          onCreated={(shopId) => setListingShopId(shopId)}
        />
      )}

      {listingShopId && (
        <CreateListingForm
          session={session}
          weespasToken={token}
          shops={shops}
          defaultShopId={listingShopId}
          onClose={() => setListingShopId(null)}
        />
      )}

      {promoteListing && (
        <PromoteChooser session={session} listing={promoteListing} onClose={() => setPromoteListing(null)} />
      )}

      {boostListing && (
        <BoostChooser session={session} listing={boostListing} onClose={() => setBoostListing(null)} />
      )}

      {flashListing && (
        <FlashSaleChooser session={session} listing={flashListing} onClose={() => setFlashListing(null)} />
      )}

      {capShop && (
        <SponsoredCapChooser session={session} shop={capShop} onClose={() => setCapShop(null)} />
      )}

      {editListing && (
        <EditListingForm
          session={session}
          weespasToken={token}
          listing={editListing}
          onClose={() => setEditListing(null)}
        />
      )}
    </div>
  );
};

export default SellerConsolePage;
