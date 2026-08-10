import React from 'react';
import './Icon.css';

export type IconSize = 'xs' | 'sm' | 'md' | 'lg' | 'xl' | 'xxl';

const iconSizeMap: Record<IconSize, number> = {
  xs: 16,
  sm: 20,
  md: 24,
  lg: 28,
  xl: 32,
  xxl: 40,
};

interface IconProps {
  icon: React.ReactNode;
  size?: IconSize;
  label?: string;
  className?: string;
  disabled?: boolean;
}

const Icon: React.FC<IconProps> = ({ icon, size = 'md', label, className, disabled }) => {
  const classes = ['icon-container', `icon-size-${size}`, className, disabled ? 'disabled' : '']
    .filter(Boolean)
    .join(' ');

  return (
    <span className={classes} role={label ? 'img' : undefined} aria-label={label} aria-hidden={label ? 'false' : 'true'}>
      {icon}
    </span>
  );
};

export default Icon;
