// LowStockCard — LEFT-column card that surfaces listings at or below the reorder signal
// (§8 Chunk E2). Sits below InquiriesCard; matches the outer shape of the other LEFT cards.
//
// Structure (grouped per shop when the seller has more than one):
//   ┌ Low stock (4) ─────────────────────────────┐
//   │  Juja Grocers                              │
//   │    Kikoi tote bag   · 1 left     [Restock] │
//   │    Maize flour 2kg  · 3 left     [Restock] │
//   │  Kilimani Kiosk                            │
//   │    Mango kilo       · 0 left     [Restock] │
//   │  Threshold: [ 5 ]  (input)                 │
//   └────────────────────────────────────────────┘
//
// The threshold is ABSOLUTE — the list is exactly the listings with stock_qty <= threshold.
// Raising it can only ever add rows. (A listing's own low_stock_threshold drives its "Low"
// badge elsewhere but deliberately does not filter this list; letting it do so meant raising
// the threshold could never surface such a listing, which read as a broken filter.)
//
// The (N) counter next to the header mirrors ViewingCard's + InquiriesCard's pattern.
// "Restock" is a shortcut to the existing EditListingForm — Chunk E1's per-row stepper on
// the dashboard is the primary +/− path; this card is a triage list, not another editor.
import React, { useState } from 'react';
import { useLowStock, LOW_STOCK_DEFAULT_FLOOR } from '../../../hooks/useLowStock';
import { lowStockCount } from '../../../api/commerce';
import type { CommerceSession, ListingOut } from '../../../api/commerce';
import './LowStockCard.css';

/** Upper bound on the threshold input, mirroring the server's accepted range. */
const MAX_FLOOR = 200;

interface LowStockCardProps {
  session: CommerceSession | null;
  /** Called when the seller clicks a Restock row → parent opens the EditListingForm modal.
   *  Optional: when absent, the row is not interactive (used in read-only preview surfaces). */
  onRestock?: (listing: ListingOut) => void;
}

const LowStockCard: React.FC<LowStockCardProps> = ({ session, onRestock }) => {
  const [floor, setFloor] = useState<number>(LOW_STOCK_DEFAULT_FLOOR);
  // The raw input text is held separately from the committed number so the field can be
  // cleared mid-edit (to type "12" you must briefly pass through ""). Binding the number
  // directly would snap an emptied field back to 0 and refetch on every keystroke.
  const [draft, setDraft] = useState<string>(String(LOW_STOCK_DEFAULT_FLOOR));
  const { data, isLoading, isError, error } = useLowStock(session, floor);

  // Server groups by shop. A single-shop seller doesn't need a header telling them the name
  // of their only shop, so headers appear only once there's something to disambiguate.
  const groups = data?.groups ?? [];
  const count = lowStockCount(data);
  const showShopHeaders = groups.length > 1;

  const onFloorChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const raw = e.target.value;
    setDraft(raw);
    if (raw === '') return;             // mid-edit; keep the last committed floor
    const n = parseInt(raw, 10);
    if (Number.isInteger(n) && n >= 0 && n <= MAX_FLOOR) setFloor(n);
  };

  // On blur, snap the text back to the committed number so the field can never be left
  // showing something the list doesn't reflect (e.g. "" or an out-of-range "999").
  const onFloorBlur = () => setDraft(String(floor));

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
        {data && count === 0 && (
          <p className="low-stock-card__state low-stock-card__state--empty" role="status">
            {`Nothing at or below ${floor}.`}
          </p>
        )}
        {data && count > 0 && (
          // Scroll region: capped at 60vh by CSS, so a long list scrolls inside the card
          // instead of pushing the Threshold control off-screen. tabIndex makes the region
          // keyboard-scrollable, which a scrollable div doesn't get for free.
          <div
            className="low-stock-card__scroll"
            tabIndex={0}
            role="group"
            aria-label="Low-stock listings"
            data-testid="low-stock-scroll"
          >
            {groups.map((g) => (
              <section key={g.shop_id} className="low-stock-card__group">
                {showShopHeaders && (
                  <h3 className="low-stock-card__group-title" data-testid="low-stock-shop-header">
                    {g.shop_name}
                  </h3>
                )}
                <ul className="low-stock-card__items">
                  {g.items.map((li) => (
                    <li key={li.id} className="low-stock-card__item" data-testid="low-stock-row">
                      <span className="low-stock-card__item-title" title={li.title}>{li.title}</span>
                      <span
                        className={
                          li.stock_qty === 0
                            ? 'low-stock-card__item-qty low-stock-card__item-qty--out'
                            : 'low-stock-card__item-qty'
                        }
                      >
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
              </section>
            ))}
          </div>
        )}
      </div>

      <footer className="low-stock-card__foot">
        <label className="low-stock-card__floor-label">
          Threshold
          <input
            type="number"
            min={0}
            max={MAX_FLOOR}
            value={draft}
            onChange={onFloorChange}
            onBlur={onFloorBlur}
            aria-label="Low-stock threshold"
            className="low-stock-card__floor-input"
          />
        </label>
        <span className="low-stock-card__floor-hint">
          Showing stock of {floor} or less
        </span>
      </footer>
    </section>
  );
};

export default LowStockCard;
