/* Subtle "hide this listing" button — appears on card hover, tooltip on focus. */

import React from 'react';
import Icon from './Icon';
import './DismissButton.css';

interface DismissButtonProps {
  onDismiss: () => void;
  className?: string;
  label?: string;
}

const DismissButton: React.FC<DismissButtonProps> = ({
  onDismiss,
  className = '',
  label = 'Hide this listing',
}) => {
  const handleClick = (e: React.MouseEvent) => {
    e.preventDefault();
    e.stopPropagation();
    onDismiss();
  };

  return (
    <button
      type="button"
      className={`dismiss-btn ${className}`}
      onClick={handleClick}
      aria-label={label}
      title={label}
    >
      <Icon name="minus" size={16} />
      <span className="dismiss-btn__tooltip" role="tooltip">{label}</span>
    </button>
  );
};

export default DismissButton;
