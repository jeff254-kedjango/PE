/* Glassmorphism price overlay for property cards */

import React from 'react';
import { formatPrice } from '../../utils/format';
import './PriceDisplay.css';

interface PriceDisplayProps {
  price: number | undefined;
  currency?: string;
  listingType?: string;
  className?: string;
}

const PriceDisplay: React.FC<PriceDisplayProps> = ({
  price,
  currency = 'KES',
  listingType,
  className = '',
}) => (
  <span className={`price-display ${className}`}>
    {formatPrice(price, currency, listingType)}
  </span>
);

export default PriceDisplay;
