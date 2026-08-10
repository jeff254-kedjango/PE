// src/components/ui/ContentModeToggle.tsx
import React from 'react';
import Icon from './Icon';
import './ContentModeToggle.css';

export type ContentMode = 'image' | 'video';

interface ContentModeToggleProps {
  mode: ContentMode;
  onChange: (mode: ContentMode) => void;
}

const ContentModeToggle: React.FC<ContentModeToggleProps> = ({ mode, onChange }) => (
  <div className="content-mode-toggle" role="tablist" aria-label="Content type">
    <button
      type="button"
      role="tab"
      aria-selected={mode === 'image'}
      className={`content-mode-toggle__btn ${mode === 'image' ? 'content-mode-toggle__btn--active' : ''}`}
      onClick={() => onChange('image')}
    >
      <Icon name="image" size={16} />
      <span>Images</span>
    </button>
    <button
      type="button"
      role="tab"
      aria-selected={mode === 'video'}
      className={`content-mode-toggle__btn ${mode === 'video' ? 'content-mode-toggle__btn--active' : ''}`}
      onClick={() => onChange('video')}
    >
      <Icon name="video" size={16} />
      <span>Videos</span>
    </button>
  </div>
);

export default ContentModeToggle;
