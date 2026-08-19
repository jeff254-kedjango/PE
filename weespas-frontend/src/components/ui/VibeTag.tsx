/* Hashtag lifestyle tag — #WorkFromHomeReady, #FamilyHome etc. */

import React from 'react';
import './VibeTag.css';

interface VibeTagProps {
  tag: string;
  className?: string;
}

const VibeTag: React.FC<VibeTagProps> = ({ tag, className = '' }) => (
  <span className={`vibe-tag ${className}`}>{tag}</span>
);

export default VibeTag;
