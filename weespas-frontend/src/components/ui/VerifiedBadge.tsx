/* Green checkmark badge for engineer-certified properties */

import React from 'react';
import Icon from './Icon';
import './VerifiedBadge.css';

interface VerifiedBadgeProps {
  size?: number;
  className?: string;
}

const VerifiedBadge: React.FC<VerifiedBadgeProps> = ({ size = 16, className = '' }) => (
  <span className={`verified-badge ${className}`} title="Engineer Certified">
    <Icon name="verified" size={size} />
  </span>
);

export default VerifiedBadge;
