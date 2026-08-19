// src/components/ui/ViewToggle.tsx
import React from 'react';
import Icon from './Icon';
import './ViewToggle.css';

export type ViewMode = 'list' | 'map';

interface ViewToggleProps {
  mode: ViewMode;
  onChange: (mode: ViewMode) => void;
}

const ViewToggle: React.FC<ViewToggleProps> = ({ mode, onChange }) => (
  <div className="view-toggle" role="tablist" aria-label="View mode">
    <button
      type="button"
      role="tab"
      aria-selected={mode === 'list'}
      className={`view-toggle__btn ${mode === 'list' ? 'view-toggle__btn--active' : ''}`}
      onClick={() => onChange('list')}
    >
      <Icon name="grid" size={16} />
      <span>List</span>
    </button>
    <button
      type="button"
      role="tab"
      aria-selected={mode === 'map'}
      className={`view-toggle__btn ${mode === 'map' ? 'view-toggle__btn--active' : ''}`}
      onClick={() => onChange('map')}
    >
      <Icon name="map" size={16} />
      <span>Map</span>
    </button>
  </div>
);

export default ViewToggle;
