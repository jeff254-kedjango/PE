// StockControl — inline POS stock adjustment for one listing on the seller dashboard.
//
// Two ways to change stock, mapping to the commerce StockAdjust "exactly one of" contract:
//   * ±1 quick buttons → {delta: ±1}   (a sale / a restock of one)
//   * "Set" with a number → {stock_qty} (a stock-take to an absolute count)
// We send exactly ONE shape per action, so we never trip the server's both/neither 422. The
// returned ListingOut carries the new count; we toast it. The mutation invalidates the dashboard
// + feed, so the row re-renders with fresh stock/visibility.
import React, { useState } from 'react';
import { useToast } from '../../../context/ToastContext';
import { useAdjustStock } from '../../../hooks/useSellerMutations';
import type { CommerceSession } from '../../../api/commerce';

interface StockControlProps {
  session: CommerceSession | null;
  listingId: string;
  stockQty: number;
}

const StockControl: React.FC<StockControlProps> = ({ session, listingId, stockQty }) => {
  const { toast } = useToast();
  const [setValue, setSetValue] = useState('');
  const adjust = useAdjustStock(session, listingId);

  const run = (body: { stock_qty?: number; delta?: number }) => {
    if (adjust.isPending) return;
    adjust.mutate(body, {
      onSuccess: (li) => toast.success(`Stock: ${li.stock_qty}`),
      onError: (err) => toast.error(err.message || 'Stock update failed.'),
    });
  };

  const submitAbsolute = (e: React.FormEvent) => {
    e.preventDefault();
    const n = parseInt(setValue, 10);
    if (!Number.isInteger(n) || n < 0) { toast.error('Enter a whole number ≥ 0.'); return; }
    run({ stock_qty: n });
    setSetValue('');
  };

  const busy = adjust.isPending;

  return (
    <div className="stock-control" data-testid="stock-control">
      <button type="button" className="stock-control__step" aria-label="Decrease stock"
              disabled={busy || stockQty <= 0} onClick={() => run({ delta: -1 })} data-testid="stock-minus">−</button>
      <span className="stock-control__count" aria-label="Current stock">{stockQty}</span>
      <button type="button" className="stock-control__step" aria-label="Increase stock"
              disabled={busy} onClick={() => run({ delta: 1 })} data-testid="stock-plus">+</button>
      <form className="stock-control__set" onSubmit={submitAbsolute}>
        <input value={setValue} onChange={(e) => setSetValue(e.target.value)} inputMode="numeric"
               placeholder="Set" aria-label="Set absolute stock" disabled={busy} />
        <button type="submit" disabled={busy || setValue.trim() === ''} data-testid="stock-set">Set</button>
      </form>
    </div>
  );
};

export default StockControl;
