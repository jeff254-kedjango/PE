// BulkStockUpload — small "Update stock in bulk" surface on the SellerDashboard (§8 Chunk E3).
//
// Two entry points into the SAME mutation:
//   * Drag-and-drop / file-input pick of a .csv file (native <input type="file">).
//   * Paste-directly textarea for a seller who doesn't want to make a file.
//
// One button (Upload) fires POST /sellers/me/stock/bulk-csv. On success the summary is shown
// inline ("Updated 12, skipped 2"); on 422 the server's detail (which names the offending
// line) is surfaced verbatim so the seller can fix and retry. All-or-nothing on the server —
// any parse error rolls the whole call back; the seller never has to guess which rows landed.
import React, { useRef, useState } from 'react';
import { useToast } from '../../../context/ToastContext';
import { useBulkStockCsv } from '../../../hooks/useSellerMutations';
import type { CommerceSession } from '../../../api/commerce';
import './BulkStockUpload.css';

interface BulkStockUploadProps {
  session: CommerceSession | null;
}

const BulkStockUpload: React.FC<BulkStockUploadProps> = ({ session }) => {
  const { toast } = useToast();
  const fileRef = useRef<HTMLInputElement>(null);
  const [csv, setCsv] = useState<string>('');
  const [feedback, setFeedback] = useState<string | null>(null);
  const mutation = useBulkStockCsv(session);

  const onFile = (file: File | null) => {
    if (!file) return;
    if (file.size > 512 * 1024) {
      toast.error('CSV too large (max 512kb).');
      return;
    }
    const reader = new FileReader();
    reader.onload = () => {
      const text = String(reader.result ?? '');
      setCsv(text);
    };
    reader.readAsText(file);
  };

  const onSubmit = () => {
    if (!session || mutation.isPending) return;
    if (csv.trim() === '') {
      toast.error('Paste or upload a CSV first.');
      return;
    }
    setFeedback(null);
    mutation.mutate(csv, {
      onSuccess: (r) => {
        const skippedNote = r.skipped_count > 0
          ? `, skipped ${r.skipped_count}` : '';
        setFeedback(`Updated ${r.updated_count}${skippedNote}.`);
        setCsv('');
        if (fileRef.current) fileRef.current.value = '';
      },
      onError: (e) => {
        // Extract the server's `detail` from the fetch layer's Error message — it's a
        // one-line "line N: <problem>" that the seller can fix and retry.
        setFeedback(`Couldn’t update: ${e.message}`);
      },
    });
  };

  return (
    <section className="bulk-stock" aria-labelledby="bulk-stock-title">
      <header className="bulk-stock__head">
        <h3 id="bulk-stock-title" className="bulk-stock__title">Bulk update stock</h3>
        <p className="bulk-stock__help">
          Upload a CSV of <code>listing_id,stock_qty</code> — all rows applied together, none if any fails.
        </p>
      </header>
      <div className="bulk-stock__inputs">
        <label className="bulk-stock__file">
          <input
            ref={fileRef}
            type="file"
            accept=".csv,text/csv"
            onChange={(e) => onFile(e.target.files?.[0] ?? null)}
            aria-label="Bulk stock CSV file"
            data-testid="bulk-stock-file"
          />
        </label>
        <textarea
          className="bulk-stock__textarea"
          rows={4}
          placeholder={'lst-abc,10\nlst-def,3'}
          value={csv}
          onChange={(e) => setCsv(e.target.value)}
          aria-label="Bulk stock CSV body"
          data-testid="bulk-stock-textarea"
        />
      </div>
      <div className="bulk-stock__actions">
        <button
          type="button"
          className="bulk-stock__submit"
          onClick={onSubmit}
          disabled={mutation.isPending}
          aria-busy={mutation.isPending}
          data-testid="bulk-stock-submit"
        >
          {mutation.isPending ? 'Uploading…' : 'Upload'}
        </button>
        {feedback && (
          <p className="bulk-stock__feedback" role="status" data-testid="bulk-stock-feedback">
            {feedback}
          </p>
        )}
      </div>
    </section>
  );
};

export default BulkStockUpload;
