// FlashSaleCard — one compact tile in the §8 Flash Sales grid.
//
// Shape: thumbnail (or an initials tile) + a "craziness" discount badge + title + the flash price
// with the comparable reference struck through beside it + a one-tap buy-now. A flash sale is always
// fixed-price (a bargain listing can't run one), so the action mirrors QuickBuyCard's FIXED path:
// one tap opens+locks a real order (openOrder) with a fresh Idempotency-Key (a double-tap can't
// create two orders), showing a brief "✓ Placed" state. Tapping the tile opens the seller storefront.
import React, { useCallback, useState } from 'react';
import Icon from '../ui/Icon';
import { resolveMediaUrl } from '../../utils/media';
import { formatPrice } from '../../utils/format';
import { openOrder, type CommerceSession, type FlashSaleItem } from '../../api/commerce';

interface FlashSaleCardProps {
  item: FlashSaleItem;
  session: CommerceSession | null;
  /** Open the seller's storefront (the tile's tap target). */
  onSelectSeller: (sellerId: string) => void;
}

type BuyState = 'idle' | 'placing' | 'placed' | 'error';

/** A stable per-intent idempotency key (crypto.randomUUID when present, else a time+random string),
 *  so the money path always carries a key even on older browsers. */
function newIdempotencyKey(): string {
  const c = (typeof crypto !== 'undefined' ? crypto : undefined) as Crypto | undefined;
  if (c && typeof c.randomUUID === 'function') return c.randomUUID();
  return `fs-${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

const FlashSaleCard: React.FC<FlashSaleCardProps> = ({ item, session, onSelectSeller }) => {
  const [state, setState] = useState<BuyState>('idle');
  const thumb = resolveMediaUrl(item.thumbnail_url);

  const handleBuy = useCallback((e: React.MouseEvent) => {
    e.stopPropagation();  // the tile opens the storefront; the button owns its own action
    if (!session || state === 'placing' || state === 'placed') return;
    setState('placing');
    openOrder(session, item.id, newIdempotencyKey())
      .then(() => setState('placed'))
      .catch(() => setState('error'));
  }, [session, state, item.id]);

  const actionLabel =
    state === 'placed' ? '✓ Placed'
    : state === 'placing' ? 'Placing…'
    : state === 'error' ? 'Try again'
    : 'Buy';

  // Only show the struck-through reference when it's genuinely higher than the flash price (it
  // always is on the server, but guard the display so we never render a nonsensical strikethrough).
  const showReference = item.reference_cents > item.flash_price_cents;

  return (
    <div
      className="flash-sale-card"
      role="button"
      tabIndex={0}
      onClick={() => onSelectSeller(item.seller_id)}
      onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onSelectSeller(item.seller_id); } }}
      aria-label={`${item.title} — ${formatPrice(item.flash_price_cents / 100, item.currency)}, ${item.discount_percent}% off`}
    >
      <div className="flash-sale-card__thumb">
        {thumb ? (
          <img src={thumb} alt={item.title} loading="lazy" />
        ) : (
          <span className="flash-sale-card__thumb-fallback" aria-hidden="true">
            {item.title.slice(0, 1).toUpperCase()}
          </span>
        )}
        {item.discount_percent > 0 && (
          <span className="flash-sale-card__badge" aria-hidden="true">
            <Icon name="bolt" size={11} /> {item.discount_percent}%
          </span>
        )}
      </div>
      <div className="flash-sale-card__body">
        <div className="flash-sale-card__title" title={item.title}>{item.title}</div>
        <div className="flash-sale-card__meta">
          <span className="flash-sale-card__price">{formatPrice(item.flash_price_cents / 100, item.currency)}</span>
          {showReference && (
            <span className="flash-sale-card__ref">{formatPrice(item.reference_cents / 100, item.currency)}</span>
          )}
        </div>
      </div>
      <button
        type="button"
        className={`flash-sale-card__buy flash-sale-card__buy--${state}`}
        onClick={handleBuy}
        disabled={state === 'placing' || state === 'placed'}
        aria-label={state === 'placed' ? `Order placed for ${item.title}` : `Buy ${item.title} now`}
        data-testid="flash-sale-buy"
      >
        <Icon name="cart" size={15} />
        <span className="flash-sale-card__buy-label">{actionLabel}</span>
      </button>
    </div>
  );
};

export default FlashSaleCard;
