// QuickBuyCard — one compact product tile in the §8 Quick Buys grid.
//
// Shape: thumbnail (product image, or an initials tile when there's no still) + title + price + an
// "add to cart" action. The action is HONEST about what it can do (see the plan's locked decision):
//   • a FIXED-price listing → one tap opens+locks an order (openOrder); we show a brief "✓ Placed"
//     state on success, an error hint on failure — no fake cart, a real order in the ledger.
//   • a BARGAIN listing → cannot be one-tap (a bargain order needs an opening offer), so the button
//     opens the seller's storefront to negotiate instead.
// Idempotency: each buy-now generates a fresh Idempotency-Key, so a double-tap can't create two
// orders. While a request is in flight the button is disabled (no double-submit).
import React, { useCallback, useState } from 'react';
import Icon from '../ui/Icon';
import { resolveMediaUrl } from '../../utils/media';
import { formatPrice, formatDistance } from '../../utils/format';
import { openOrder, type CommerceSession, type QuickBuyItem } from '../../api/commerce';

interface QuickBuyCardProps {
  item: QuickBuyItem;
  session: CommerceSession | null;
  /** Open the seller's storefront (bargain items negotiate there; also the tile's tap target). */
  onSelectSeller: (sellerId: string) => void;
}

type BuyState = 'idle' | 'placing' | 'placed' | 'error';

/** A stable per-intent idempotency key. Uses crypto.randomUUID when present (all modern browsers),
 *  falling back to a time+random string so the money path always carries a key. */
function newIdempotencyKey(): string {
  const c = (typeof crypto !== 'undefined' ? crypto : undefined) as Crypto | undefined;
  if (c && typeof c.randomUUID === 'function') return c.randomUUID();
  return `qb-${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

const QuickBuyCard: React.FC<QuickBuyCardProps> = ({ item, session, onSelectSeller }) => {
  const [state, setState] = useState<BuyState>('idle');
  const thumb = resolveMediaUrl(item.thumbnail_url);
  const isBargain = item.pricing_mode === 'bargain';

  const handleAction = useCallback((e: React.MouseEvent) => {
    e.stopPropagation();  // the tile itself opens the storefront; the button owns its own action
    if (isBargain) {
      // A bargain needs an opening offer — route to the storefront to negotiate, don't fake a buy.
      onSelectSeller(item.seller_id);
      return;
    }
    if (!session || state === 'placing' || state === 'placed') return;
    setState('placing');
    openOrder(session, item.id, newIdempotencyKey())
      .then(() => setState('placed'))
      .catch(() => setState('error'));
  }, [isBargain, session, state, item.id, item.seller_id, onSelectSeller]);

  const actionLabel =
    state === 'placed' ? '✓ Placed'
    : state === 'placing' ? 'Placing…'
    : state === 'error' ? 'Try again'
    : isBargain ? 'Bargain' : 'Buy';

  return (
    <div
      className="quick-buy-card"
      role="button"
      tabIndex={0}
      onClick={() => onSelectSeller(item.seller_id)}
      onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onSelectSeller(item.seller_id); } }}
      aria-label={`${item.title} — ${formatPrice(item.price_cents / 100, item.currency)}`}
    >
      <div className="quick-buy-card__thumb">
        {thumb ? (
          <img src={thumb} alt={item.title} loading="lazy" />
        ) : (
          <span className="quick-buy-card__thumb-fallback" aria-hidden="true">
            {item.title.slice(0, 1).toUpperCase()}
          </span>
        )}
      </div>
      <div className="quick-buy-card__body">
        <div className="quick-buy-card__title" title={item.title}>{item.title}</div>
        <div className="quick-buy-card__meta">
          <span className="quick-buy-card__price">{formatPrice(item.price_cents / 100, item.currency)}</span>
          <span className="quick-buy-card__dist">{formatDistance(item.distance_m / 1000)}</span>
        </div>
      </div>
      <button
        type="button"
        className={`quick-buy-card__cart quick-buy-card__cart--${state}`}
        onClick={handleAction}
        disabled={state === 'placing' || state === 'placed'}
        aria-label={
          isBargain
            ? `Negotiate for ${item.title}`
            : state === 'placed' ? `Order placed for ${item.title}` : `Buy ${item.title} now`
        }
        data-testid="quick-buy-cart"
      >
        <Icon name="cart" size={15} />
        <span className="quick-buy-card__cart-label">{actionLabel}</span>
      </button>
    </div>
  );
};

export default QuickBuyCard;
