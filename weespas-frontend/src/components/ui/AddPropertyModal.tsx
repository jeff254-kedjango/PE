import React, { useState, useEffect, useCallback, useRef } from 'react';
import { createPortal } from 'react-dom';
import { useQueryClient } from '@tanstack/react-query';
import { useToast } from '../../context/ToastContext';
import { useGeolocation } from '../../hooks/useGeolocation';
import { createProperty, uploadPropertyImages, uploadPropertyVideo } from '../../api/properties';
import type { ListingType, PropertyCategory, PropertyCreatePayload } from '../../types/propertyApi';
import Icon from './Icon';
import './AddPropertyModal.css';

interface AddPropertyModalProps {
  isOpen: boolean;
  onClose: () => void;
  token: string | null;
}

interface PropertyFormData {
  title: string;
  description: string;
  listing_type: string;
  category: string;
  price: string;
  currency: string;
  location_name: string;
  latitude: string;
  longitude: string;
  bedrooms: string;
  bathrooms: string;
  size: string;
  size_numeric: string;
  parking_spaces: string;
  year_built: string;
  is_engineer_certified: boolean;
}

const INITIAL_FORM: PropertyFormData = {
  title: '',
  description: '',
  listing_type: '',
  category: '',
  price: '',
  currency: 'KES',
  location_name: '',
  latitude: '',
  longitude: '',
  bedrooms: '',
  bathrooms: '',
  size: '',
  size_numeric: '',
  parking_spaces: '',
  year_built: '',
  is_engineer_certified: false,
};

const LISTING_TYPES: { value: ListingType; label: string }[] = [
  { value: 'sale', label: 'For Sale' },
  { value: 'rent', label: 'For Rent' },
];

const CATEGORIES: { value: PropertyCategory; label: string }[] = [
  { value: 'house', label: 'House' },
  { value: 'apartment', label: 'Apartment' },
  { value: 'villa', label: 'Villa' },
  { value: 'studio', label: 'Studio' },
  { value: 'office', label: 'Office' },
  { value: 'land', label: 'Land' },
  { value: 'warehouse', label: 'Warehouse' },
  { value: 'shop', label: 'Shop' },
  { value: 'kiosk', label: 'Kiosk' },
  { value: 'container', label: 'Container' },
  { value: 'stall', label: 'Stall' },
  { value: 'commercial_space', label: 'Commercial Space' },
  { value: 'other', label: 'Other' },
];

const CURRENCIES = ['KES', 'USD', 'EUR', 'GBP'];

const CURRENT_YEAR = new Date().getFullYear();

const AddPropertyModal: React.FC<AddPropertyModalProps> = ({ isOpen, onClose, token }) => {
  const { toast } = useToast();
  const toastRef = useRef(toast);
  toastRef.current = toast;
  const queryClient = useQueryClient();
  const geo = useGeolocation();
  const [form, setForm] = useState<PropertyFormData>(INITIAL_FORM);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [submitting, setSubmitting] = useState(false);
  const [locationUsed, setLocationUsed] = useState(false);
  const [imageFiles, setImageFiles] = useState<File[]>([]);
  const [imagePreviews, setImagePreviews] = useState<string[]>([]);
  const [videoFile, setVideoFile] = useState<File | null>(null);
  const [uploadStep, setUploadStep] = useState('');
  const imageInputRef = useRef<HTMLInputElement>(null);
  const videoInputRef = useRef<HTMLInputElement>(null);

  // Reset form when modal opens
  useEffect(() => {
    if (isOpen) {
      setForm(INITIAL_FORM);
      setErrors({});
      setSubmitting(false);
      setLocationUsed(false);
      setImageFiles([]);
      setImagePreviews((prev) => { prev.forEach(URL.revokeObjectURL); return []; });
      setVideoFile(null);
      setUploadStep('');
    }
  }, [isOpen]);

  // When geolocation resolves, fill lat/lng and clear their errors
  useEffect(() => {
    if (geo.latitude !== null && geo.longitude !== null && !geo.loading) {
      setForm((prev) => ({
        ...prev,
        latitude: geo.latitude!.toFixed(6),
        longitude: geo.longitude!.toFixed(6),
      }));
      setErrors((prev) => {
        const next = { ...prev };
        delete next.latitude;
        delete next.longitude;
        return next;
      });
      setLocationUsed(true);
      toastRef.current.success('Location detected');
    }
  }, [geo.latitude, geo.longitude, geo.loading]);

  // Show error toast if geolocation fails
  useEffect(() => {
    if (geo.error) {
      toastRef.current.error(geo.error);
    }
  }, [geo.error]);

  // Close on Escape
  useEffect(() => {
    if (!isOpen) return;
    const handleKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', handleKey);
    return () => document.removeEventListener('keydown', handleKey);
  }, [isOpen, onClose]);

  // Lock body scroll
  useEffect(() => {
    if (isOpen) {
      document.body.style.overflow = 'hidden';
    }
    return () => { document.body.style.overflow = ''; };
  }, [isOpen]);

  const handleText = useCallback(
    (field: keyof PropertyFormData) => (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement>) => {
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

  const handleImageSelect = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
    const files = e.target.files;
    if (!files || files.length === 0) return;
    const newFiles = Array.from(files);

    setImageFiles((prev) => {
      const total = prev.length + newFiles.length;
      if (total > 20) {
        toastRef.current.error('Maximum 20 images allowed');
        return prev;
      }
      // Create previews only when files are accepted
      const newPreviews = newFiles.map((f) => URL.createObjectURL(f));
      setImagePreviews((p) => [...p, ...newPreviews]);
      return [...prev, ...newFiles];
    });

    // Reset input so same files can be re-selected
    e.target.value = '';
  }, []);

  const removeImage = useCallback((index: number) => {
    setImagePreviews((prev) => {
      URL.revokeObjectURL(prev[index]);
      return prev.filter((_, i) => i !== index);
    });
    setImageFiles((prev) => prev.filter((_, i) => i !== index));
  }, []);

  const handleVideoSelect = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    if (file.size > 100 * 1024 * 1024) {
      toastRef.current.error('Video must be under 100 MB');
      e.target.value = '';
      return;
    }
    setVideoFile(file);
    e.target.value = '';
  }, []);

  const removeVideo = useCallback(() => {
    setVideoFile(null);
  }, []);

  const formatFileSize = useCallback((bytes: number) => {
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  }, []);

  const validate = useCallback((): Record<string, string> => {
    const errs: Record<string, string> = {};

    if (!form.title.trim()) errs.title = 'Title is required';
    else if (form.title.length > 255) errs.title = 'Title must be under 255 characters';

    if (!form.listing_type) errs.listing_type = 'Select a listing type';
    if (!form.category) errs.category = 'Select a category';

    if (!form.price.trim()) errs.price = 'Price is required';
    else if (isNaN(Number(form.price)) || Number(form.price) <= 0) errs.price = 'Enter a valid price greater than 0';

    if (!form.location_name.trim()) errs.location_name = 'Location name is required';
    else if (form.location_name.length > 255) errs.location_name = 'Location must be under 255 characters';

    if (!form.latitude.trim()) errs.latitude = 'Latitude is required';
    else {
      const lat = Number(form.latitude);
      if (isNaN(lat) || lat < -90 || lat > 90) errs.latitude = 'Must be between -90 and 90';
    }

    if (!form.longitude.trim()) errs.longitude = 'Longitude is required';
    else {
      const lng = Number(form.longitude);
      if (isNaN(lng) || lng < -180 || lng > 180) errs.longitude = 'Must be between -180 and 180';
    }

    // Optional field validation
    if (form.year_built.trim()) {
      const yr = Number(form.year_built);
      if (isNaN(yr) || yr < 1900 || yr > CURRENT_YEAR) errs.year_built = `Must be 1900\u2013${CURRENT_YEAR}`;
    }

    if (form.bedrooms.trim() && (isNaN(Number(form.bedrooms)) || Number(form.bedrooms) < 0)) {
      errs.bedrooms = 'Must be 0 or more';
    }
    if (form.bathrooms.trim() && (isNaN(Number(form.bathrooms)) || Number(form.bathrooms) < 0)) {
      errs.bathrooms = 'Must be 0 or more';
    }
    if (form.parking_spaces.trim() && (isNaN(Number(form.parking_spaces)) || Number(form.parking_spaces) < 0)) {
      errs.parking_spaces = 'Must be 0 or more';
    }
    if (form.size_numeric.trim() && (isNaN(Number(form.size_numeric)) || Number(form.size_numeric) < 0)) {
      errs.size_numeric = 'Must be 0 or more';
    }

    return errs;
  }, [form]);

  const handleSubmit = useCallback(async () => {
    const validationErrors = validate();
    if (Object.keys(validationErrors).length > 0) {
      setErrors(validationErrors);
      return;
    }

    if (!token) {
      toast.error('You must be logged in to create a property');
      return;
    }

    setSubmitting(true);

    const payload: PropertyCreatePayload = {
      title: form.title.trim(),
      price: Number(form.price),
      listing_type: form.listing_type as ListingType,
      category: form.category as PropertyCategory,
      location_name: form.location_name.trim(),
      latitude: Number(form.latitude),
      longitude: Number(form.longitude),
    };

    // Add optional fields only if provided
    if (form.description.trim()) payload.description = form.description.trim();
    if (form.currency !== 'KES') payload.currency = form.currency;
    if (form.is_engineer_certified) payload.is_engineer_certified = true;
    if (form.bedrooms.trim()) payload.bedrooms = Number(form.bedrooms);
    if (form.bathrooms.trim()) payload.bathrooms = Number(form.bathrooms);
    if (form.size.trim()) payload.size = form.size.trim();
    if (form.size_numeric.trim()) payload.size_numeric = Number(form.size_numeric);
    if (form.parking_spaces.trim()) payload.parking_spaces = Number(form.parking_spaces);
    if (form.year_built.trim()) payload.year_built = Number(form.year_built);

    try {
      setUploadStep('Creating property...');
      const property = await createProperty(token, payload);

      // Upload images if any selected
      if (imageFiles.length > 0) {
        setUploadStep(`Uploading ${imageFiles.length} image${imageFiles.length > 1 ? 's' : ''}...`);
        try {
          await uploadPropertyImages(token, property.id, imageFiles);
        } catch (err) {
          console.error('[AddPropertyModal] image upload failed', err);
          const detail = err instanceof Error ? err.message : 'Unknown error';
          toast.error(`Image upload failed: ${detail}`);
        }
      }

      // Upload video if selected
      if (videoFile) {
        setUploadStep('Uploading video...');
        try {
          await uploadPropertyVideo(token, property.id, videoFile);
        } catch (err) {
          console.error('[AddPropertyModal] video upload failed', err);
          const detail = err instanceof Error ? err.message : 'Unknown error';
          toast.error(`Video upload failed: ${detail}`);
        }
      }

      // The listing is saved; its InSAR footprint verification runs in the
      // background and the result lands in the notification bell (30 min – 24 hr).
      toast.success(
        'We are verifying your details… You\'ll get a notification once it\'s done. ' +
        'This usually takes 30 min – 24 hr. Thank you for using weespas — signals that matter!',
        8000,
      );
      queryClient.invalidateQueries({ queryKey: ['agentProperties'] });
      queryClient.invalidateQueries({ queryKey: ['agentStats'] });
      onClose();
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to create property';
      toast.error(message);
    } finally {
      setSubmitting(false);
      setUploadStep('');
    }
  }, [form, token, validate, toast, queryClient, onClose, imageFiles, videoFile]);

  if (!isOpen) return null;

  const errClass = (field: string) => errors[field] ? ' prop-input--error' : '';

  return createPortal(
    <div className="adv-modal-overlay" onClick={onClose}>
      <div
        className="adv-modal"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-label="Add New Property"
      >
        {/* Header */}
        <header className="adv-modal__header">
          <div className="adv-modal__header-left">
            <Icon name="home" size={18} />
            <h2>Add New Property</h2>
          </div>
          <button type="button" className="adv-modal__close" onClick={onClose} aria-label="Close">
            <Icon name="x" size={20} />
          </button>
        </header>

        {/* Body */}
        <div className="adv-modal__body">
          {/* ── Section 1: Basic Info ── */}
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
                  placeholder="e.g. Modern 3BR Apartment in Westlands"
                  maxLength={255}
                  value={form.title}
                  onChange={handleText('title')}
                  className={errClass('title')}
                  aria-required="true"
                />
                {errors.title && <span className="prop-error">{errors.title}</span>}
              </div>
            </div>

            <div className="adv-row prop-row-gap">
              <div className="adv-field prop-field--full">
                <label>Description</label>
                <textarea
                  className={`prop-textarea${errClass('description')}`}
                  placeholder="Describe the property features, nearby amenities..."
                  value={form.description}
                  onChange={handleText('description')}
                  rows={3}
                />
              </div>
            </div>

            <div className="adv-row prop-row-gap">
              <div className="adv-field">
                <label>Listing Type <span className="prop-required">*</span></label>
                <select
                  className={`prop-select${!form.listing_type ? ' prop-select--empty' : ''}${errClass('listing_type')}`}
                  value={form.listing_type}
                  onChange={handleText('listing_type')}
                  aria-required="true"
                >
                  <option value="" disabled>Select type</option>
                  {LISTING_TYPES.map((lt) => (
                    <option key={lt.value} value={lt.value}>{lt.label}</option>
                  ))}
                </select>
                {errors.listing_type && <span className="prop-error">{errors.listing_type}</span>}
              </div>
              <div className="adv-field">
                <label>Category <span className="prop-required">*</span></label>
                <select
                  className={`prop-select${!form.category ? ' prop-select--empty' : ''}${errClass('category')}`}
                  value={form.category}
                  onChange={handleText('category')}
                  aria-required="true"
                >
                  <option value="" disabled>Select category</option>
                  {CATEGORIES.map((c) => (
                    <option key={c.value} value={c.value}>{c.label}</option>
                  ))}
                </select>
                {errors.category && <span className="prop-error">{errors.category}</span>}
              </div>
            </div>

            <div className="adv-row prop-row-gap">
              <div className="adv-field">
                <label>Price <span className="prop-required">*</span></label>
                <input
                  type="number"
                  min={1}
                  step="any"
                  placeholder="e.g. 50000"
                  value={form.price}
                  onChange={handleText('price')}
                  className={errClass('price')}
                  aria-required="true"
                />
                {errors.price && <span className="prop-error">{errors.price}</span>}
              </div>
              <div className="adv-field">
                <label>Currency</label>
                <select
                  className="prop-select"
                  value={form.currency}
                  onChange={handleText('currency')}
                >
                  {CURRENCIES.map((c) => (
                    <option key={c} value={c}>{c}</option>
                  ))}
                </select>
              </div>
            </div>
          </section>

          {/* ── Section 2: Location ── */}
          <section className="adv-section">
            <h3 className="adv-section__title">
              <Icon name="mapPin" size={16} />
              Location
            </h3>

            <div className="adv-row">
              <div className="adv-field prop-field--full">
                <label>Location Name <span className="prop-required">*</span></label>
                <input
                  type="text"
                  placeholder="e.g. Westlands, Nairobi"
                  maxLength={255}
                  value={form.location_name}
                  onChange={handleText('location_name')}
                  className={errClass('location_name')}
                  aria-required="true"
                />
                {errors.location_name && <span className="prop-error">{errors.location_name}</span>}
              </div>
            </div>

            <button
              type="button"
              className={`prop-location-btn prop-row-gap${geo.loading ? ' locating' : ''}${locationUsed ? ' located' : ''}`}
              onClick={geo.requestLocation}
              disabled={geo.loading}
            >
              <Icon name="crosshair" size={18} />
              {geo.loading
                ? 'Finding your location...'
                : locationUsed
                  ? 'Location detected'
                  : 'Use My Location'}
            </button>

            <div className="prop-location-divider">or enter coordinates</div>

            <div className="adv-row">
              <div className="adv-field">
                <label>Latitude <span className="prop-required">*</span></label>
                <input
                  type="number"
                  step="any"
                  placeholder="e.g. -1.2921"
                  value={form.latitude}
                  onChange={handleText('latitude')}
                  className={errClass('latitude')}
                  aria-required="true"
                />
                {errors.latitude && <span className="prop-error">{errors.latitude}</span>}
              </div>
              <div className="adv-field">
                <label>Longitude <span className="prop-required">*</span></label>
                <input
                  type="number"
                  step="any"
                  placeholder="e.g. 36.8219"
                  value={form.longitude}
                  onChange={handleText('longitude')}
                  className={errClass('longitude')}
                  aria-required="true"
                />
                {errors.longitude && <span className="prop-error">{errors.longitude}</span>}
              </div>
            </div>
          </section>

          {/* ── Section 3: Property Details ── */}
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
                  placeholder="Any"
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
                  placeholder="Any"
                  value={form.bathrooms}
                  onChange={handleText('bathrooms')}
                  className={errClass('bathrooms')}
                />
                {errors.bathrooms && <span className="prop-error">{errors.bathrooms}</span>}
              </div>
            </div>

            <div className="adv-row prop-row-gap">
              <div className="adv-field">
                <label>Size (label)</label>
                <input
                  type="text"
                  placeholder='e.g. "1200 sqft"'
                  value={form.size}
                  onChange={handleText('size')}
                />
              </div>
              <div className="adv-field">
                <label>Size (numeric, sq ft)</label>
                <input
                  type="number"
                  min={0}
                  step="any"
                  placeholder="e.g. 1200"
                  value={form.size_numeric}
                  onChange={handleText('size_numeric')}
                  className={errClass('size_numeric')}
                />
                {errors.size_numeric && <span className="prop-error">{errors.size_numeric}</span>}
              </div>
            </div>

            <div className="adv-row prop-row-gap">
              <div className="adv-field">
                <label>Parking Spaces</label>
                <input
                  type="number"
                  min={0}
                  placeholder="Any"
                  value={form.parking_spaces}
                  onChange={handleText('parking_spaces')}
                  className={errClass('parking_spaces')}
                />
                {errors.parking_spaces && <span className="prop-error">{errors.parking_spaces}</span>}
              </div>
              <div className="adv-field">
                <label>Year Built</label>
                <input
                  type="number"
                  min={1900}
                  max={CURRENT_YEAR}
                  placeholder={`e.g. ${CURRENT_YEAR - 5}`}
                  value={form.year_built}
                  onChange={handleText('year_built')}
                  className={errClass('year_built')}
                />
                {errors.year_built && <span className="prop-error">{errors.year_built}</span>}
              </div>
            </div>
          </section>

          {/* ── Section 4: Certification ── */}
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

          {/* ── Section 5: Photos & Video ── */}
          <section className="adv-section">
            <h3 className="adv-section__title">
              <Icon name="image" size={16} />
              Photos & Video
            </h3>

            {/* Images */}
            <p className="prop-media-label">Images (max 20, 10 MB each)</p>
            <div
              className="prop-upload-zone"
              onClick={() => imageInputRef.current?.click()}
              role="button"
              tabIndex={0}
              onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') imageInputRef.current?.click(); }}
            >
              <Icon name="upload" size={24} />
              <span className="prop-upload-zone__label">
                {imageFiles.length > 0 ? 'Add more images' : 'Select images'}
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

            {imagePreviews.length > 0 && (
              <div className="prop-preview-grid">
                {imagePreviews.map((src, i) => (
                  <div key={i} className="prop-preview-item">
                    <img src={src} alt={`Preview ${i + 1}`} loading="lazy" />
                    <button
                      type="button"
                      className="prop-preview-remove"
                      onClick={() => removeImage(i)}
                      aria-label={`Remove image ${i + 1}`}
                    >
                      <Icon name="x" size={12} />
                    </button>
                  </div>
                ))}
              </div>
            )}

            {/* Video */}
            <p className="prop-media-label">Video (max 1, 100 MB)</p>
            {!videoFile ? (
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
            ) : (
              <div className="prop-video-preview">
                <Icon name="video" size={20} />
                <div className="prop-video-preview__info">
                  <div className="prop-video-preview__name">{videoFile.name}</div>
                  <div className="prop-video-preview__size">{formatFileSize(videoFile.size)}</div>
                </div>
                <button
                  type="button"
                  className="prop-video-remove"
                  onClick={removeVideo}
                  aria-label="Remove video"
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

            <p className="prop-note prop-row-gap">
              <Icon name="info" size={14} />
              You can also add media after creating the property.
            </p>
          </section>
        </div>

        {/* Footer */}
        <footer className="adv-modal__footer prop-modal-footer">
          <button type="button" className="adv-modal__clear" onClick={onClose}>
            Cancel
          </button>
          <button
            type="button"
            className="adv-modal__apply"
            onClick={handleSubmit}
            disabled={submitting}
          >
            <Icon name="plus" size={16} />
            {submitting ? (uploadStep || 'Creating...') : 'Create Property'}
          </button>
        </footer>
      </div>
    </div>,
    document.body
  );
};

export default AddPropertyModal;
