/* Generic pill badge for category labels, status indicators */

import React from 'react';
import './Badge.css';

interface BadgeProps {
  children: React.ReactNode;
  variant?: 'default' | 'success' | 'accent' | 'muted';
  className?: string;
}

const Badge: React.FC<BadgeProps> = ({ children, variant = 'default', className = '' }) => (
  <span className={`badge badge--${variant} ${className}`}>{children}</span>
);

export default Badge;
