/* Shimmer loading placeholder matching PropertyCard dimensions */

import React from 'react';
import './SkeletonCard.css';

const SkeletonCard: React.FC = () => (
  <div className="skeleton-card" aria-hidden="true">
    <div className="skeleton-card__image skeleton" />
    <div className="skeleton-card__body">
      <div className="skeleton skeleton-card__title" />
      <div className="skeleton skeleton-card__location" />
      <div className="skeleton skeleton-card__specs" />
      <div className="skeleton-card__footer">
        <div className="skeleton skeleton-card__tag" />
        <div className="skeleton skeleton-card__tag" />
      </div>
    </div>
  </div>
);

export default SkeletonCard;
