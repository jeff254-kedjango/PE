// TradeMediaUploader — pick listing images (+ one optional short video) before publishing.
//
// A controlled picker: the parent CreateListingForm owns the selected File[] / video File and does
// the actual upload (via uploadTradeMedia, weespas token) at submit time. This component only
// validates client-side (count, per-file size, type) and renders previews — mirroring the
// AddPropertyModal uploader UX (hidden input + drop zone + preview grid + objectURL revoke).
//
// Client-side caps mirror the weespas endpoint (the server is the authority and 413/400s anyway):
// images ≤10 MB each, ≤20; one video ≤250 MB. The 250 MB cap is the §8 short-video limit.
import React, { useCallback, useEffect, useRef, useState } from 'react';
import './TradeMediaUploader.css';

const MAX_IMAGES = 20;
const MAX_IMAGE_BYTES = 10 * 1024 * 1024;
const MAX_VIDEO_BYTES = 250 * 1024 * 1024;
const IMAGE_ACCEPT = 'image/jpeg,image/png,image/webp,image/avif';
const VIDEO_ACCEPT = 'video/mp4,video/webm,video/quicktime';

/** Imperative handle so a parent can open a file picker directly.
 *
 *  The composer's action row ("Post Pictures" / "Post a Video") must land the user ON the file
 *  dialog in one click. A browser only opens that dialog from a trusted user gesture, so the parent
 *  cannot fake it with state — it has to call `.click()` on the real <input> during the event. This
 *  handle exposes exactly that, and nothing else. */
export interface TradeMediaUploaderHandle {
  pickImages: () => void;
  pickVideo: () => void;
}

interface TradeMediaUploaderProps {
  images: File[];
  video: File | null;
  onImagesChange: (files: File[]) => void;
  onVideoChange: (file: File | null) => void;
  /** Surface a validation message to the user (parent wires this to a toast). */
  onError: (message: string) => void;
  disabled?: boolean;
}

const TradeMediaUploader = React.forwardRef<TradeMediaUploaderHandle, TradeMediaUploaderProps>(({
  images, video, onImagesChange, onVideoChange, onError, disabled,
}, ref) => {
  const imageInputRef = useRef<HTMLInputElement>(null);
  const videoInputRef = useRef<HTMLInputElement>(null);

  // Guard the picker on `disabled` too — otherwise the composer's action row could open a dialog
  // mid-submit, letting the file set change under an in-flight upload.
  React.useImperativeHandle(ref, () => ({
    pickImages: () => { if (!disabled) imageInputRef.current?.click(); },
    pickVideo: () => { if (!disabled) videoInputRef.current?.click(); },
  }), [disabled]);

  // Object URLs for previews — created lazily, revoked when the file set changes/unmounts.
  const imageUrls = useMemoObjectUrls(images);
  const videoUrl = useMemoObjectUrl(video);

  const addImages = useCallback((files: FileList | null) => {
    if (!files) return;
    const incoming = Array.from(files);
    const tooBig = incoming.find((f) => f.size > MAX_IMAGE_BYTES);
    if (tooBig) { onError(`"${tooBig.name}" is over 10 MB.`); return; }
    const next = [...images, ...incoming].slice(0, MAX_IMAGES);
    if (images.length + incoming.length > MAX_IMAGES) {
      onError(`Up to ${MAX_IMAGES} images per listing.`);
    }
    onImagesChange(next);
  }, [images, onImagesChange, onError]);

  const setVideo = useCallback((files: FileList | null) => {
    const f = files?.[0] ?? null;
    if (f && f.size > MAX_VIDEO_BYTES) {
      onError(`Video is over 250 MB — trim it or pick a smaller clip.`);
      return;
    }
    onVideoChange(f);
  }, [onVideoChange, onError]);

  return (
    <div className="trade-uploader">
      {/* Images */}
      <div className="trade-uploader__group">
        <span className="trade-uploader__label">Photos <em>(up to {MAX_IMAGES}, ≤10 MB each)</em></span>
        {/* Honest traction nudge, shown on BOTH surfaces (this uploader is shared by the listing form
            and the timeline composer). It states the real ranking behaviour — listings with a clear
            photo get a small visibility boost in the buyer feed (services/ranking.py media term) —
            without over-promising: it is a nudge, never a requirement, so a photo-less post is fine. */}
        <p className="trade-uploader__hint">A clear photo helps buyers find you — listings with photos get more traction.</p>
        <div className="trade-uploader__grid">
          {imageUrls.map((url, i) => (
            <div key={url} className="trade-uploader__thumb">
              <img src={url} alt={`Selected ${i + 1}`} />
              <button
                type="button"
                className="trade-uploader__remove"
                aria-label="Remove image"
                disabled={disabled}
                onClick={() => onImagesChange(images.filter((_, j) => j !== i))}
              >×</button>
            </div>
          ))}
          {images.length < MAX_IMAGES && (
            <button
              type="button"
              className="trade-uploader__add"
              disabled={disabled}
              onClick={() => imageInputRef.current?.click()}
              data-testid="add-images"
            >+ Add</button>
          )}
        </div>
        <input
          ref={imageInputRef}
          type="file"
          accept={IMAGE_ACCEPT}
          multiple
          hidden
          onChange={(e) => { addImages(e.target.files); e.target.value = ''; }}
        />
      </div>

      {/* Optional short video */}
      <div className="trade-uploader__group">
        <span className="trade-uploader__label">Short video <em>(optional, ≤250 MB)</em></span>
        {video ? (
          <div className="trade-uploader__video">
            {videoUrl && <video src={videoUrl} controls preload="metadata" />}
            <button
              type="button"
              className="trade-uploader__remove trade-uploader__remove--video"
              aria-label="Remove video"
              disabled={disabled}
              onClick={() => onVideoChange(null)}
            >Remove video</button>
          </div>
        ) : (
          <button
            type="button"
            className="trade-uploader__add"
            disabled={disabled}
            onClick={() => videoInputRef.current?.click()}
            data-testid="add-video"
          >+ Add video</button>
        )}
        <input
          ref={videoInputRef}
          type="file"
          accept={VIDEO_ACCEPT}
          hidden
          onChange={(e) => { setVideo(e.target.files); e.target.value = ''; }}
        />
      </div>
    </div>
  );
});

// forwardRef erases the inferred name in React DevTools / test output — set it back.
TradeMediaUploader.displayName = 'TradeMediaUploader';

// --- small object-URL helpers (create on change, revoke on cleanup — no leaks) ---
// State (not a ref) so a new URL triggers a re-render with the preview; the effect revokes the
// PREVIOUS batch when the files change and on unmount, so blob URLs never accumulate.

function useMemoObjectUrls(files: File[]): string[] {
  const [urls, setUrls] = useState<string[]>([]);
  useEffect(() => {
    const created = files.map((f) => URL.createObjectURL(f));
    setUrls(created);
    return () => created.forEach((u) => URL.revokeObjectURL(u));
  }, [files]);
  return urls;
}

function useMemoObjectUrl(file: File | null): string | null {
  const [url, setUrl] = useState<string | null>(null);
  useEffect(() => {
    if (!file) { setUrl(null); return; }
    const u = URL.createObjectURL(file);
    setUrl(u);
    return () => URL.revokeObjectURL(u);
  }, [file]);
  return url;
}

export default TradeMediaUploader;
