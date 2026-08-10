// ShopImagePicker — a single-image picker for the shop's logo (avatar) or banner. Shows a live
// object-URL preview (circle for the logo, wide for the banner), a hint line, and a remove control.
// Purely presentational: the parent owns the File state and does the upload on submit (via the
// weespas media pipeline). Mirrors TradeMediaUploader's file-input idiom but for one image.
import React, { useEffect, useRef, useState } from 'react';
import './ShopImagePicker.css';

const IMAGE_ACCEPT = 'image/jpeg,image/png,image/webp,image/avif';

interface ShopImagePickerProps {
  id: string;
  label: string;
  /** Explanatory copy under the control (e.g. "this is your business logo"). */
  hint: string;
  file: File | null;
  onChange: (file: File | null) => void;
  disabled?: boolean;
  /** Preview shape: a square/circle for a logo, a wide strip for a banner. */
  shape: 'circle' | 'wide';
  testid?: string;
}

const ShopImagePicker: React.FC<ShopImagePickerProps> = ({
  id, label, hint, file, onChange, disabled, shape, testid,
}) => {
  const inputRef = useRef<HTMLInputElement>(null);
  const [preview, setPreview] = useState<string | null>(null);

  // Build (and revoke) an object URL for the live preview — revoke on change/unmount so we never
  // leak blob URLs.
  useEffect(() => {
    if (!file) { setPreview(null); return; }
    const url = URL.createObjectURL(file);
    setPreview(url);
    return () => URL.revokeObjectURL(url);
  }, [file]);

  return (
    <div className="seller-field shop-image">
      <label htmlFor={id}>{label} <span className="seller-field__hint">(optional)</span></label>
      <div className="shop-image__row">
        <div className={`shop-image__preview shop-image__preview--${shape}`} aria-hidden="true">
          {preview ? <img src={preview} alt="" /> : <span className="shop-image__placeholder">＋</span>}
        </div>
        <div className="shop-image__controls">
          <input
            ref={inputRef} id={id} type="file" accept={IMAGE_ACCEPT} disabled={disabled}
            className="shop-image__input" data-testid={testid}
            onChange={(e) => onChange(e.target.files?.[0] ?? null)}
          />
          <button
            type="button" className="seller-btn seller-btn--ghost shop-image__btn" disabled={disabled}
            onClick={() => inputRef.current?.click()}
          >
            {file ? 'Change' : 'Choose image'}
          </button>
          {file && (
            <button
              type="button" className="shop-image__remove" disabled={disabled}
              onClick={() => { onChange(null); if (inputRef.current) inputRef.current.value = ''; }}
            >
              Remove
            </button>
          )}
        </div>
      </div>
      <span className="seller-field__hint seller-field__hint--block">{hint}</span>
    </div>
  );
};

export default ShopImagePicker;
