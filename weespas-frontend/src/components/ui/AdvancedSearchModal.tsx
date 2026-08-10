import React, { useState, useEffect, useCallback } from 'react';
import { PropertyFilterParams } from '../../types/propertyApi';
import Icon from './Icon';
import './AdvancedSearchModal.css';

interface AdvancedSearchModalProps {
  isOpen: boolean;
  onClose: () => void;
  filters: PropertyFilterParams;
  onApply: (filters: Partial<PropertyFilterParams>) => void;
}

/** Fields managed exclusively by this modal (not in SearchPanel) */
type AdvancedFields = Pick<
  PropertyFilterParams,
  'bedrooms' | 'bathrooms' | 'parking_spaces' | 'year_built' | 'min_size' | 'max_size' | 'engineer_certified' | 'is_featured' | 'city' | 'county'
>;

const CURRENT_YEAR = new Date().getFullYear();

const AdvancedSearchModal: React.FC<AdvancedSearchModalProps> = ({ isOpen, onClose, filters, onApply }) => {
  const [local, setLocal] = useState<AdvancedFields>({});

  // Sync local state when modal opens
  useEffect(() => {
    if (isOpen) {
      setLocal({
        bedrooms: filters.bedrooms,
        bathrooms: filters.bathrooms,
        parking_spaces: filters.parking_spaces,
        year_built: filters.year_built,
        min_size: filters.min_size,
        max_size: filters.max_size,
        engineer_certified: filters.engineer_certified,
        is_featured: filters.is_featured,
        city: filters.city,
        county: filters.county,
      });
    }
  }, [isOpen, filters]);

  // Close on Escape
  useEffect(() => {
    if (!isOpen) return;
    const handleKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', handleKey);
    return () => document.removeEventListener('keydown', handleKey);
  }, [isOpen, onClose]);

  // Lock body scroll while open
  useEffect(() => {
    if (isOpen) {
      document.body.style.overflow = 'hidden';
    }
    return () => { document.body.style.overflow = ''; };
  }, [isOpen]);

  const handleNumber = useCallback(
    (field: keyof AdvancedFields) => (e: React.ChangeEvent<HTMLInputElement>) => {
      const val = e.target.value;
      setLocal((prev) => ({ ...prev, [field]: val === '' ? undefined : Number(val) }));
    },
    []
  );

  const handleText = useCallback(
    (field: keyof AdvancedFields) => (e: React.ChangeEvent<HTMLInputElement>) => {
      const val = e.target.value;
      setLocal((prev) => ({ ...prev, [field]: val || undefined }));
    },
    []
  );

  const handleToggle = useCallback(
    (field: 'engineer_certified' | 'is_featured') => () => {
      setLocal((prev) => ({ ...prev, [field]: prev[field] ? undefined : true }));
    },
    []
  );

  const activeCount = Object.values(local).filter(
    (v) => v !== undefined && v !== '' && v !== false
  ).length;

  const handleApply = () => {
    onApply(local);
    onClose();
  };

  const handleClear = () => {
    setLocal({
      bedrooms: undefined,
      bathrooms: undefined,
      parking_spaces: undefined,
      year_built: undefined,
      min_size: undefined,
      max_size: undefined,
      engineer_certified: undefined,
      is_featured: undefined,
      city: undefined,
      county: undefined,
    });
  };

  if (!isOpen) return null;

  return (
    <div className="adv-modal-overlay" onClick={onClose}>
      <div
        className="adv-modal"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-label="Advanced Search Filters"
      >
        {/* Header */}
        <header className="adv-modal__header">
          <div className="adv-modal__header-left">
            <Icon name="filter" size={18} />
            <h2>Advanced Filters</h2>
          </div>
          <button type="button" className="adv-modal__close" onClick={onClose} aria-label="Close">
            <Icon name="x" size={20} />
          </button>
        </header>

        {/* Body */}
        <div className="adv-modal__body">
          {/* ── Location (text-based) ── */}
          <section className="adv-section">
            <h3 className="adv-section__title">
              <Icon name="mapPin" size={16} />
              Location
            </h3>
            <div className="adv-row">
              <div className="adv-field">
                <label>City</label>
                <input
                  type="text"
                  placeholder="e.g. Nairobi"
                  value={local.city ?? ''}
                  onChange={handleText('city')}
                />
              </div>
              <div className="adv-field">
                <label>County</label>
                <input
                  type="text"
                  placeholder="e.g. Nairobi"
                  value={local.county ?? ''}
                  onChange={handleText('county')}
                />
              </div>
            </div>
          </section>

          {/* ── Size Range ── */}
          <section className="adv-section">
            <h3 className="adv-section__title">
              <Icon name="ruler" size={16} />
              Size (sq ft)
            </h3>
            <div className="adv-row">
              <div className="adv-field">
                <label>Min size</label>
                <input
                  type="number"
                  min={0}
                  placeholder="0"
                  value={local.min_size ?? ''}
                  onChange={handleNumber('min_size')}
                />
              </div>
              <div className="adv-field">
                <label>Max size</label>
                <input
                  type="number"
                  min={0}
                  placeholder="Any"
                  value={local.max_size ?? ''}
                  onChange={handleNumber('max_size')}
                />
              </div>
            </div>
          </section>

          {/* ── Beds, Baths, Parking & Year Built ── */}
          <section className="adv-section">
            <h3 className="adv-section__title">
              <Icon name="parking" size={16} />
              Property Details
            </h3>
            <div className="adv-row">
              <div className="adv-field">
                <label>Beds</label>
                <input
                  type="number"
                  min={0}
                  placeholder="Any"
                  value={local.bedrooms ?? ''}
                  onChange={handleNumber('bedrooms')}
                />
              </div>
              <div className="adv-field">
                <label>Baths</label>
                <input
                  type="number"
                  min={0}
                  placeholder="Any"
                  value={local.bathrooms ?? ''}
                  onChange={handleNumber('bathrooms')}
                />
              </div>
            </div>
            <div className="adv-row">
              <div className="adv-field">
                <label>Min parking spaces</label>
                <input
                  type="number"
                  min={0}
                  placeholder="Any"
                  value={local.parking_spaces ?? ''}
                  onChange={handleNumber('parking_spaces')}
                />
              </div>
              <div className="adv-field">
                <label>Year built (from)</label>
                <input
                  type="number"
                  min={1900}
                  max={CURRENT_YEAR}
                  placeholder="e.g. 2010"
                  value={local.year_built ?? ''}
                  onChange={handleNumber('year_built')}
                />
              </div>
            </div>
          </section>

          {/* ── Toggles ── */}
          <section className="adv-section">
            <h3 className="adv-section__title">
              <Icon name="check" size={16} />
              Certifications & Features
            </h3>
            <div className="adv-toggles">
              <button
                type="button"
                className={`adv-toggle-chip ${local.engineer_certified ? 'active' : ''}`}
                onClick={handleToggle('engineer_certified')}
              >
                <Icon name="verified" size={16} />
                Engineer Certified
              </button>
              <button
                type="button"
                className={`adv-toggle-chip ${local.is_featured ? 'active' : ''}`}
                onClick={handleToggle('is_featured')}
              >
                <Icon name="check" size={16} />
                Featured Only
              </button>
            </div>
          </section>
        </div>

        {/* Footer */}
        <footer className="adv-modal__footer">
          <button type="button" className="adv-modal__clear" onClick={handleClear}>
            Clear All
          </button>
          <button type="button" className="adv-modal__apply" onClick={handleApply}>
            Apply Filters
            {activeCount > 0 && <span className="adv-modal__badge">{activeCount}</span>}
          </button>
        </footer>
      </div>
    </div>
  );
};

export default AdvancedSearchModal;
