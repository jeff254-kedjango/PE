/* Animated heart button for favoriting properties */

import React from 'react';
import { useToast } from '../../context/ToastContext';
import useHeartPop from '../../hooks/useHeartPop';
import Icon from './Icon';
import './FavoriteButton.css';

interface FavoriteButtonProps {
  active: boolean;
  onToggle: () => void;
  className?: string;
}

const FavoriteButton: React.FC<FavoriteButtonProps> = ({ active, onToggle, className = '' }) => {
  const { popping, pop } = useHeartPop();
  const { toast } = useToast();

  const handleClick = (e: React.MouseEvent) => {
    e.preventDefault(); /* Prevent card link navigation */
    e.stopPropagation();
    pop();
    onToggle();
    // active = current state before toggle; !active = new state
    toast.success(!active ? 'Added to favorites' : 'Removed from favorites');
  };

  return (
    <button
      type="button"
      className={`favorite-btn ${active ? 'favorite-btn--active' : ''} ${className}`}
      onClick={handleClick}
      aria-label={active ? 'Remove from favorites' : 'Add to favorites'}
    >
      <Icon name={active ? 'heartFilled' : 'heart'} size={20} className={popping ? 'animate-heart' : ''} />
    </button>
  );
};

export default FavoriteButton;
