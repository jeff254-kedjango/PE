// ShopPage — the /shop/:key page route (§8 storefront).
//
// ONE route, ONE component. The `:key` param carries either:
//   * a handle prefixed with "@"  (canonical shareable URL, e.g. /shop/@mama-mboga)
//   * a bare sellerId              (legacy fallback for shops without a handle)
// The "@" prefix disambiguates the two families in a single URL slot — no route ordering
// ambiguity, no shadow matches. When arrived via sellerId AND the resolved storefront has a
// handle, we Navigate replace to /shop/@<handle> (frontend-only canonical redirect, §8).
//
// Session gating: this page is buyer-visible; useCommerceSession lazily provisions a token so an
// anonymous URL still resolves. While the session is provisioning, Storefront shows its own
// skeleton (the hook is disabled until session arrives, which is the correct "waiting" state).
//
// Back-navigation: a persistent "← Back to Trade" link sits ABOVE the Storefront on the page
// mount. We use <Link to="/trade">, NOT navigate(-1), so a buyer who arrived via a shared URL
// with no history still has a way HOME. The sheet mount (used from /trade itself) doesn't need
// this — it has its own close button, and closing returns to the feed underneath naturally.
import React from 'react';
import { Link, Navigate, useParams } from 'react-router-dom';
import { useCommerceSession } from '../hooks/useCommerceSession';
import { useStorefront } from '../hooks/useStorefront';
import Storefront from '../components/trade/Storefront';
import './ShopPage.css';

/** Shared page-mount chrome: the "back to Trade" link that sits above the Storefront. Kept as
 *  its own component so both the handle branch and the sellerId branch use the SAME chrome
 *  without duplicating markup. */
const ShopPageShell: React.FC<{ children: React.ReactNode }> = ({ children }) => (
  <div className="shop-page" data-testid="shop-page">
    <div className="shop-page__topbar">
      <Link to="/trade" className="shop-page__back" data-testid="shop-page-back">
        <span aria-hidden="true">←</span> Back to Trade
      </Link>
    </div>
    {children}
  </div>
);

const ShopPage: React.FC = () => {
  const { key = '' } = useParams<{ key: string }>();
  const { session } = useCommerceSession();

  // Handle path: "@handle" — the "@" is a marker only, we strip it before passing to the API.
  if (key.startsWith('@')) {
    const handle = key.slice(1);
    return (
      <ShopPageShell>
        <Storefront session={session} entry={{ handle }} />
      </ShopPageShell>
    );
  }

  // Legacy sellerId path — resolve once, redirect to the canonical handle URL if the shop has one.
  return <ShopBySellerIdInner sellerId={key} session={session} />;
};

/** Split out so the useStorefront hook only runs on the sellerId branch — keeps the handle branch
 *  from doing a redundant read the Storefront will already do internally. React Query dedupes on
 *  the shared key, so this second consumer piggybacks on the same fetch Storefront makes. */
const ShopBySellerIdInner: React.FC<{
  sellerId: string;
  session: ReturnType<typeof useCommerceSession>['session'];
}> = ({ sellerId, session }) => {
  const q = useStorefront(session, sellerId || null);
  const handle = q.data?.shops[0]?.shop.handle ?? null;
  if (handle) {
    // `replace` so the browser back button still points at wherever the buyer came from, not the
    // stale sellerId form.
    return <Navigate to={`/shop/@${encodeURIComponent(handle)}`} replace />;
  }
  return (
    <ShopPageShell>
      <Storefront session={session} entry={{ sellerId }} />
    </ShopPageShell>
  );
};

export default ShopPage;
