import React, { useState, useEffect, useCallback, useRef, useMemo } from 'react';
import { createPortal } from 'react-dom';
import { useQueryClient } from '@tanstack/react-query';
import { useToast } from '../../context/ToastContext';
import {
  updateProperty,
  uploadPropertyImages,
  uploadPropertyVideo,
  deletePropertyImage,
  deletePropertyVideo,
} from '../../api/properties';
import type { Property, PropertyUpdatePayload, PropertyImage, PropertyVideo } from '../../types/propertyApi';
import Icon from './Icon';
import './AddPropertyModal.css';

interface EditPropertyModalProps {
  isOpen: boolean;
  onClose: () => void;
  token: string | null;
  property: Property;
}

interface EditFormData {
  title: string;
  description: string;
  price: string;
  bedrooms: string;
  bathrooms: string;
  is_engineer_certified: boolean;
}

const EditPropertyModal: React.FC<EditPropertyModalProps> = ({ isOpen, onClose, token, property }) => {
  const { toast } = useToast();
  const toastRef = useRef(toast);
  toastRef.current = toast;
  const queryClient = useQueryClient();
  const [form, setForm] = useState<EditFormData>({
    title: '',
    description: '',
    price: '',
    bedrooms: '',
    bathrooms: '',
    is_engineer_certified: false,
  });
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [submitting, setSubmitting] = useState(false);
  const [uploadStep, setUploadStep] = useState('');

  // --- Media editing state ---
  // Existing media: which server-side IDs are still kept (those not in deletedIds)
  const [deletedImageIds, setDeletedImageIds] = useState<Set<string>>(new Set());
  const [deletedVideoIds, setDeletedVideoIds] = useState<Set<string>>(new Set());
  // New uploads pending submit
  const [newImageFiles, setNewImageFiles] = useState<File[]>([]);
  const [newImagePreviews, setNewImagePreviews] = useState<string[]>([]);
  const [newVideoFile, setNewVideoFile] = useState<File | null>(null);

  const imageInputRef = useRef<HTMLInputElement>(null);
  const videoInputRef = useRef<HTMLInputElement>(null);

  // Reset on open. Resetting on `property.id` too so switching properties
  // without remount doesn't leak previews from the prior property.
  useEffect(() => {
    if (isOpen) {
      setForm({
        title: property.title ?? '',
        description: property.description ?? '',
        price: property.price != null ? String(property.price) : '',
        bedrooms: property.bedrooms != null ? String(property.bedrooms) : '',
        bathrooms: property.bathrooms != null ? String(property.bathrooms) : '',
        is_engineer_certified: property.is_engineer_certified ?? false,
      });
      setErrors({});
      setSubmitting(false);
      setUploadStep('');
      setDeletedImageIds(new Set());
      setDeletedVideoIds(new Set());
      setNewImageFiles([]);
      setNewImagePreviews((prev) => { prev.forEach(URL.revokeObjectURL); return []; });
      setNewVideoFile(null);
    }
  }, [isOpen, property.id]);

  // Revoke any unfreed object URLs on unmount as a final safety net
  // (long-lived agent sessions otherwise leak File-backed Blobs).
  useEffect(() => {
    return () => { newImagePreviews.forEach(URL.revokeObjectURL); };
    // Intentionally only on unmount — running this on every change would
    // revoke URLs still rendered by <img>.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (!isOpen) return;
    const handleKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    document.addEventListener('keydown', handleKey);
    return () => document.removeEventListener('keydown', handleKey);
  }, [isOpen, onClose]);

  useEffect(() => {
    if (isOpen) document.body.style.overflow = 'hidden';
    return () => { document.body.style.overflow = ''; };
  }, [isOpen]);

  const handleText = useCallback(
    (field: keyof EditFormData) => (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) => {
      const val = e.target.value;
      setForm((prev) => ({ ...prev, [field]: val }));
      setErrors((prev) => {
        if (!prev[field]) return prev;
        const next = { ...prev };
        delete next[field];
        return next;
      });
    },
    []
  );

  const handleToggle = useCallback(() => {
    setForm((prev) => ({ ...prev, is_engineer_certified: !prev.is_engineer_certified }));
  }, []);

  // Existing media filtered by deletion set — memoized to keep referential
  // stability for the rendered list across unrelated state changes.
  const existingImages = useMemo<PropertyImage[]>(
    () => (property.images ?? []).filter((img) => !deletedImageIds.has(img.id)),
    [property.images, deletedImageIds]
  );
  const existingVideos = useMemo<PropertyVideo[]>(
    () => (property.videos ?? []).filter((v) => !deletedVideoIds.has(v.id)),
    [property.videos, deletedVideoIds]
  );

  const totalImageCount = existingImages.length + newImageFiles.length;
  const totalVideoCount = existingVideos.length + (newVideoFile ? 1 : 0);

  const handleImageSelect = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
    const files = e.target.files;
    if (!files || files.length === 0) return;
    const incoming = Array.from(files);

    setNewImageFiles((prev) => {
      const projectedTotal = existingImages.length + prev.length + incoming.length;
      if (projectedTotal > 20) {
        toastRef.current.error('Maximum 20 images per property');
        return prev;
      }
      const newPreviews = incoming.map((f) => URL.createObjectURL(f));
      setNewImagePreviews((p) => [...p, ...newPreviews]);
      return [...prev, ...incoming];
    });
    e.target.value = '';
  }, [existingImages.length]);

  const removeNewImage = useCallback((index: number) => {
    setNewImagePreviews((prev) => {
      URL.revokeObjectURL(prev[index]);
      return prev.filter((_, i) => i !== index);
    });
    setNewImageFiles((prev) => prev.filter((_, i) => i !== index));
  }, []);

  const markImageForDeletion = useCallback((imageId: string) => {
    setDeletedImageIds((prev) => {
      const next = new Set(prev);
      next.add(imageId);
      return next;
    });
  }, []);

  const undoImageDeletion = useCallback((imageId: string) => {
    setDeletedImageIds((prev) => {
      const next = new Set(prev);
      next.delete(imageId);
      return next;
    });
  }, []);

  const handleVideoSelect = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    if (file.size > 100 * 1024 * 1024) {
      toastRef.current.error('Video must be under 100 MB');
      e.target.value = '';
      return;
    }
    if (existingVideos.length > 0) {
      toastRef.current.error('Remove the existing video before uploading a new one');
      e.target.value = '';
      return;
    }
    setNewVideoFile(file);
    e.target.value = '';
  }, [existingVideos.length]);

  const removeNewVideo = useCallback(() => {
    setNewVideoFile(null);
  }, []);

  const markVideoForDeletion = useCallback((videoId: string) => {
    setDeletedVideoIds((prev) => {
      const next = new Set(prev);
      next.add(videoId);
      return next;
    });
  }, []);

  const undoVideoDeletion = useCallback((videoId: string) => {
    setDeletedVideoIds((prev) => {
      const next = new Set(prev);
      next.delete(videoId);
      return next;
    });
  }, []);

  const formatFileSize = useCallback((bytes: number) => {
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  }, []);

  const validate = useCallback((): Record<string, string> => {
    const errs: Record<string, string> = {};
    if (!form.title.trim()) errs.title = 'Title is required';
    else if (form.title.length > 255) errs.title = 'Title must be under 255 characters';

    if (!form.price.trim()) errs.price = 'Price is required';
    else if (isNaN(Number(form.price)) || Number(form.price) <= 0) errs.price = 'Enter a valid price greater than 0';

    if (form.bedrooms.trim() && (isNaN(Number(form.bedrooms)) || Number(form.bedrooms) < 0)) {
      errs.bedrooms = 'Must be 0 or more';
    }
    if (form.bathrooms.trim() && (isNaN(Number(form.bathrooms)) || Number(form.bathrooms) < 0)) {
      errs.bathrooms = 'Must be 0 or more';
    }
    return errs;
  }, [form]);

  const buildPayload = useCallback((): PropertyUpdatePayload | null => {
    const payload: PropertyUpdatePayload = {};
    let hasChanges = false;

    if (form.title.trim() !== (property.title ?? '')) {
      payload.title = form.title.trim();
      hasChanges = true;
    }
    if (form.description.trim() !== (property.description ?? '')) {
      payload.description = form.description.trim();
      hasChanges = true;
    }

    const newPrice = Number(form.price);
    if (property.price == null || newPrice !== property.price) {
      payload.price = newPrice;
      hasChanges = true;
    }

    const newBedrooms = form.bedrooms.trim() ? Number(form.bedrooms) : undefined;
    const oldBedrooms = property.bedrooms ?? undefined;
    if (newBedrooms !== oldBedrooms) {
      payload.bedrooms = newBedrooms ?? 0;
      hasChanges = true;
    }

    const newBathrooms = form.bathrooms.trim() ? Number(form.bathrooms) : undefined;
    const oldBathrooms = property.bathrooms ?? undefined;
    if (newBathrooms !== oldBathrooms) {
      payload.bathrooms = newBathrooms ?? 0;
      hasChanges = true;
    }

    if (form.is_engineer_certified !== (property.is_engineer_certified ?? false)) {
      payload.is_engineer_certified = form.is_engineer_certified;
      hasChanges = true;
    }

    return hasChanges ? payload : null;
  }, [form, property]);

  const handleSubmit = useCallback(async () => {
    const validationErrors = validate();
    if (Object.keys(validationErrors).length > 0) {
      setErrors(validationErrors);
      return;
    }

    if (!token) {
      toast.error('You must be logged in to update a property');
      return;
    }

    const payload = buildPayload();
    const hasMediaChanges =
      deletedImageIds.size > 0 ||
      deletedVideoIds.size > 0 ||
      newImageFiles.length > 0 ||
      newVideoFile !== null;

    if (!payload && !hasMediaChanges) {
      toast.info('No changes to save');
      return;
    }

    setSubmitting(true);
    try {
      if (payload) {
        setUploadStep('Saving details...');
        await updateProperty(token, property.id, payload);
      }

      // Run deletions in parallel — each is independent and the user has
      // already confirmed by clicking save.
      if (deletedImageIds.size > 0 || deletedVideoIds.size > 0) {
        setUploadStep('Removing media...');
        await Promise.all([
          ...Array.from(deletedImageIds).map((id) => deletePropertyImage(token, property.id, id)),
          ...Array.from(deletedVideoIds).map((id) => deletePropertyVideo(token, property.id, id)),
        ]);
      }

      if (newImageFiles.length > 0) {
        setUploadStep(`Uploading ${newImageFiles.length} image${newImageFiles.length > 1 ? 's' : ''}...`);
        await uploadPropertyImages(token, property.id, newImageFiles);
      }

      if (newVideoFile) {
        setUploadStep('Uploading video...');
        await uploadPropertyVideo(token, property.id, newVideoFile);
      }

      toast.success('Property updated successfully');
      // Targeted cache invalidation — only the agent's lists & stats, not
      // the full public feed.
      queryClient.invalidateQueries({ queryKey: ['agentProperties'] });
      queryClient.invalidateQueries({ queryKey: ['agentStats'] });
      onClose();
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to update property';
      toast.error(message);
    } finally {
      setSubmitting(false);
      setUploadStep('');
    }
  }, [
    validate, token, buildPayload, property.id, toast, queryClient, onClose,
    deletedImageIds, deletedVideoIds, newImageFiles, newVideoFile,
  ]);

  if (!isOpen) return null;

  const errClass = (field: string) => errors[field] ? ' prop-input--error' : '';
  const deletedImagesList = (property.images ?? []).filter((img) => deletedImageIds.has(img.id));
  const deletedVideosList = (property.videos ?? []).filter((v) => deletedVideoIds.has(v.id));

  return createPortal(
    <div className="adv-modal-overlay" onClick={onClose}>
      <div
        className="adv-modal"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-label="Edit Property"
      >
        <header className="adv-modal__header">
          <div className="adv-modal__header-left">
            <Icon name="edit" size={18} />
            <h2>Edit Property</h2>
          </div>
          <button type="button" className="adv-modal__close" onClick={onClose} aria-label="Close">
            <Icon name="x" size={20} />
          </button>
        </header>

        <div className="adv-modal__body">
          {/* Basic Info */}
          <section className="adv-section">
            <h3 className="adv-section__title">
              <Icon name="home" size={16} />
              Basic Info
            </h3>

            <div className="adv-row">
              <div className="adv-field prop-field--full">
                <label>Title <span className="prop-required">*</span></label>
                <input
                  type="text"
                  maxLength={255}
                  value={form.title}
                  onChange={handleText('title')}
                  className={errClass('title')}
                />
                {errors.title && <span className="prop-error">{errors.title}</span>}
              </div>
            </div>

            <div className="adv-row prop-row-gap">
              <div className="adv-field prop-field--full">
                <label>Description</label>
                <textarea
                  className={`prop-textarea${errClass('description')}`}
                  value={form.description}
                  onChange={handleText('description')}
                  rows={3}
                />
              </div>
            </div>

            <div className="adv-row prop-row-gap">
              <div className="adv-field">
                <label>Price <span className="prop-required">*</span></label>
                <input
                  type="number"
                  min={1}
                  step="any"
                  value={form.price}
                  onChange={handleText('price')}
                  className={errClass('price')}
                />
                {errors.price && <span className="prop-error">{errors.price}</span>}
              </div>
            </div>
          </section>

          {/* Property Details */}
          <section className="adv-section">
            <h3 className="adv-section__title">
              <Icon name="bed" size={16} />
              Property Details
            </h3>

            <div className="adv-row">
              <div className="adv-field">
                <label>Bedrooms</label>
                <input
                  type="number"
                  min={0}
                  value={form.bedrooms}
                  onChange={handleText('bedrooms')}
                  className={errClass('bedrooms')}
                />
                {errors.bedrooms && <span className="prop-error">{errors.bedrooms}</span>}
              </div>
              <div className="adv-field">
                <label>Bathrooms</label>
                <input
                  type="number"
                  min={0}
                  value={form.bathrooms}
                  onChange={handleText('bathrooms')}
                  className={errClass('bathrooms')}
                />
                {errors.bathrooms && <span className="prop-error">{errors.bathrooms}</span>}
              </div>
            </div>
          </section>

          {/* Certification */}
          <section className="adv-section">
            <h3 className="adv-section__title">
              <Icon name="verified" size={16} />
              Certification
            </h3>
            <div className="adv-toggles">
              <button
                type="button"
                className={`adv-toggle-chip ${form.is_engineer_certified ? 'active' : ''}`}
                onClick={handleToggle}
              >
                <Icon name="verified" size={16} />
                Engineer Certified
              </button>
            </div>
          </section>

          {/* Media */}
          <section className="adv-section">
            <h3 className="adv-section__title">
              <Icon name="image" size={16} />
              Photos & Video
            </h3>

            {/* Existing + new images */}
            <p className="prop-media-label">
              Images ({totalImageCount}/20, 10 MB each)
            </p>
            <div
              className="prop-upload-zone"
              onClick={() => imageInputRef.current?.click()}
              role="button"
              tabIndex={0}
              onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') imageInputRef.current?.click(); }}
            >
              <Icon name="upload" size={24} />
              <span className="prop-upload-zone__label">
                {totalImageCount > 0 ? 'Add more images' : 'Select images'}
              </span>
              <span className="prop-upload-zone__hint">JPEG, PNG, WebP, AVIF</span>
            </div>
            <input
              ref={imageInputRef}
              type="file"
              className="prop-upload-input"
              accept="image/jpeg,image/png,image/webp,image/avif"
              multiple
              onChange={handleImageSelect}
            />

            {(existingImages.length > 0 || newImagePreviews.length > 0) && (
              <div className="prop-preview-grid">
                {existingImages.map((img) => (
                  <div key={img.id} className="prop-preview-item">
                    <img
                      src={img.thumbnail_url || img.url}
                      alt={img.alt_text || 'Property image'}
                      loading="lazy"
                      decoding="async"
                    />
                    <button
                      type="button"
                      className="prop-preview-remove"
                      onClick={() => markImageForDeletion(img.id)}
                      aria-label="Remove image"
                    >
                      <Icon name="x" size={12} />
                    </button>
                  </div>
                ))}
                {newImagePreviews.map((src, i) => (
                  <div key={`new-${i}`} className="prop-preview-item">
                    <img src={src} alt={`New image ${i + 1}`} loading="lazy" decoding="async" />
                    <button
                      type="button"
                      className="prop-preview-remove"
                      onClick={() => removeNewImage(i)}
                      aria-label={`Remove new image ${i + 1}`}
                    >
                      <Icon name="x" size={12} />
                    </button>
                  </div>
                ))}
              </div>
            )}

            {deletedImagesList.length > 0 && (
              <p className="prop-media-label" style={{ marginTop: 8 }}>
                {deletedImagesList.length} image{deletedImagesList.length > 1 ? 's' : ''} marked for deletion.{' '}
                <button
                  type="button"
                  className="prop-link-btn"
                  onClick={() => deletedImagesList.forEach((img) => undoImageDeletion(img.id))}
                >
                  Undo
                </button>
              </p>
            )}

            {/* Video */}
            <p className="prop-media-label">Video ({totalVideoCount}/1, 100 MB)</p>

            {existingVideos.map((video) => (
              <div key={video.id} className="prop-video-preview">
                <Icon name="video" size={20} />
                <div className="prop-video-preview__info">
                  <div className="prop-video-preview__name">{video.title || 'Property video'}</div>
                  {video.file_size != null && (
                    <div className="prop-video-preview__size">{formatFileSize(video.file_size)}</div>
                  )}
                </div>
                <button
                  type="button"
                  className="prop-video-remove"
                  onClick={() => markVideoForDeletion(video.id)}
                  aria-label="Remove video"
                >
                  <Icon name="trash" size={14} />
                </button>
              </div>
            ))}

            {existingVideos.length === 0 && !newVideoFile && (
              <div
                className="prop-upload-zone"
                onClick={() => videoInputRef.current?.click()}
                role="button"
                tabIndex={0}
                onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') videoInputRef.current?.click(); }}
              >
                <Icon name="video" size={24} />
                <span className="prop-upload-zone__label">Select video</span>
                <span className="prop-upload-zone__hint">MP4, WebM, MOV</span>
              </div>
            )}

            {newVideoFile && (
              <div className="prop-video-preview">
                <Icon name="video" size={20} />
                <div className="prop-video-preview__info">
                  <div className="prop-video-preview__name">{newVideoFile.name}</div>
                  <div className="prop-video-preview__size">{formatFileSize(newVideoFile.size)}</div>
                </div>
                <button
                  type="button"
                  className="prop-video-remove"
                  onClick={removeNewVideo}
                  aria-label="Remove new video"
                >
                  <Icon name="trash" size={14} />
                </button>
              </div>
            )}

            <input
              ref={videoInputRef}
              type="file"
              className="prop-upload-input"
              accept="video/mp4,video/webm,video/quicktime"
              onChange={handleVideoSelect}
            />

            {deletedVideosList.length > 0 && (
              <p className="prop-media-label">
                Video marked for deletion.{' '}
                <button
                  type="button"
                  className="prop-link-btn"
                  onClick={() => deletedVideosList.forEach((v) => undoVideoDeletion(v.id))}
                >
                  Undo
                </button>
              </p>
            )}
          </section>
        </div>

        <footer className="adv-modal__footer prop-modal-footer">
          {uploadStep && <span className="prop-upload-step">{uploadStep}</span>}
          <button type="button" className="adv-modal__clear" onClick={onClose} disabled={submitting}>
            Cancel
          </button>
          <button
            type="button"
            className="adv-modal__apply"
            onClick={handleSubmit}
            disabled={submitting}
          >
            <Icon name="check" size={16} />
            {submitting ? 'Saving...' : 'Save Changes'}
          </button>
        </footer>
      </div>
    </div>,
    document.body
  );
};

export default EditPropertyModal;
