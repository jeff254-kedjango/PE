import React from 'react';
import Icon from './Icon';
import './ConfirmedShield.css';

interface ConfirmedShieldProps {
  size?: number;
  className?: string;
}

/**
 * ConfirmedShield — a small green shield marking a listing whose InSAR building has a
 * recorded on-the-ground assessment by a certifier (engineer/authority). It is a
 * GROUND-VERIFIED PROVENANCE marker ("a human assessed this building"), NOT a safety
 * verdict — the title says so, and risk/danger is shown separately. Icon-only (no text)
 * to avoid crowding the listing card. Mirrors VerifiedBadge.
 */
const ConfirmedShield: React.FC<ConfirmedShieldProps> = ({ size = 16, className = '' }) => (
  <span className={`confirmed-shield ${className}`} title="Confirmed by an on-the-ground assessment">
    <Icon name="shield" size={size} />
  </span>
);

export default ConfirmedShield;
