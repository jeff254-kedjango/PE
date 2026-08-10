/* "FOR SALE" / "FOR RENT" overlay badge on property cards */

import React from 'react';
import './ListingTypeBadge.css';

interface ListingTypeBadgeProps {
  type: string | undefined;
  className?: string;
}

const ListingTypeBadge: React.FC<ListingTypeBadgeProps> = ({ type, className = '' }) => {
  if (!type) return null;

  const isSale = type === 'sale';
  const label = isSale ? 'FOR SALE' : 'FOR RENT';

  return (
    <span className={`listing-badge listing-badge--${isSale ? 'sale' : 'rent'} ${className}`}>
      {label}
    </span>
  );
};

export default ListingTypeBadge;
