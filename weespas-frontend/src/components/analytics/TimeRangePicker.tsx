import React from 'react';
import type { SinceWindow } from '../../types/analytics';
import './analytics.css';

interface Props {
  value: SinceWindow;
  onChange: (next: SinceWindow) => void;
}

const OPTIONS: { value: SinceWindow; label: string }[] = [
  { value: '7d', label: '7d' },
  { value: '30d', label: '30d' },
  { value: '90d', label: '90d' },
  { value: 'all', label: 'All' },
];

const TimeRangePicker: React.FC<Props> = ({ value, onChange }) => (
  <div className="time-range-picker" role="tablist" aria-label="Time range">
    {OPTIONS.map((o) => (
      <button
        key={o.value}
        type="button"
        role="tab"
        aria-selected={value === o.value}
        className={value === o.value ? 'is-on' : ''}
        onClick={() => onChange(o.value)}
      >
        {o.label}
      </button>
    ))}
  </div>
);

export default TimeRangePicker;
