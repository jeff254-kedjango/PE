// LowStockCard — LEFT-column card that surfaces listings at or below the reorder signal
// (§8 Chunk E2). Sits below InquiriesCard; matches the outer shape of the other LEFT cards.
//
// Structure:
//   ┌ Low stock (3) ─────────────────────────────┐
//   │  Kikoi tote bag     · 1 left     [Restock] │
//   │  Maize flour 2kg    · 3 left     [Restock] │
//   │  Mango kilo         · 5 left     [Restock] │
//   │  Threshold: [ 5 ]  (input)                 │
//   └────────────────────────────────────────────┘
//
// The (N) counter next to the header mirrors ViewingCard's + InquiriesCard's pattern.
// "Restock" is a shortcut to the existing EditListingForm — Chunk E1's per-row stepper on
// the dashboard is the primary +/− path; this card is a triage list, not another editor.
import React, { useState } from 'react';
import { useLowStock, LOW_STOCK_DEFAULT_FLOOR } from '../../../hooks/useLowStock';
import type { CommerceSession, ListingOut } from '../../../api/commerce';
import './LowStockCard.css';

interface LowStockCardProps {
  session: CommerceSession | null;
  /** Called when the seller clicks a Restock row → parent opens the EditListingForm modal.
   *  Optional: when absent, the row is not interactive (used in read-only preview surfaces). */
  onRestock?: (listing: ListingOut) => void;
}

const LowStockCard: React.FC<LowStockCardProps> = ({ session, onRestock }) => {
  const [floor, setFloor] = useState<number>(LOW_STOCK_DEFAULT_FLOOR);
  const { data, isLoading, isError, error } = useLowStock(session, floor);

  const items = data?.items ?? [];
  const count = items.length;

  const onFloorChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const n = parseInt(e.target.value, 10);
    if (Number.isInteger(n) && n >= 0 && n <= 200) setFloor(n);
    else if (e.target.value === '') setFloor(0);
  };

  return (
    <section className="low-stock-card" aria-labelledby="low-stock-card-title">
      <header className="low-stock-card__head">
        <h2 id="low-stock-card-title" className="low-stock-card__title">
          Low stock
          {count > 0 && (
            <span className="low-stock-card__count" aria-label={`${count} low`}>
              {' '}({count})
            </span>
          )}
        </h2>
      </header>

      <div className="low-stock-card__body">
        {isLoading && <p className="low-stock-card__state" role="status">Checking stock…</p>}
        {isError && (
          <p className="low-stock-card__state low-stock-card__state--error" role="alert">
            Couldn’t load stock alerts. {error?.message ?? ''}
          </p>
        )}
        {data && items.length === 0 && (
          <p className="low-stock-card__state low-stock-card__state--empty" role="status">
            All stock healthy.
          </p>
        )}
        {data && items.length > 0 && (
          <ul className="low-stock-card__items" aria-label="Low-stock listings">
            {items.map((li) => (
              <li key={li.id} className="low-stock-card__item" data-testid="low-stock-row">
                <span className="low-stock-card__item-title" title={li.title}>{li.title}</span>
                <span className="low-stock-card__item-qty">
                  {li.stock_qty === 0 ? 'Out of stock' : `${li.stock_qty} left`}
                </span>
                {onRestock && (
                  <button
                    type="button"
                    className="low-stock-card__restock"
                    onClick={() => onRestock(li)}
                    data-testid="low-stock-restock"
                  >
                    Restock
                  </button>
                )}
              </li>
            ))}
          </ul>
        )}
      </div>

      <footer className="low-stock-card__foot">
        <label className="low-stock-card__floor-label">
          Threshold
          <input
            type="number"
            min={0}
            max={200}
            value={floor}
            onChange={onFloorChange}
            aria-label="Low-stock threshold"
            className="low-stock-card__floor-input"
          />
        </label>
      </footer>
    </section>
  );
};

export default LowStockCard;
