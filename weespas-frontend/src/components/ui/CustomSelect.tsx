import React, { useState, useRef, useEffect, useCallback } from 'react';
import Icon from './Icon';
import './CustomSelect.css';

export interface SelectOption {
  label: string;
  value: string;
}

interface CustomSelectProps {
  options: SelectOption[];
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  id?: string;
}

const CustomSelect: React.FC<CustomSelectProps> = ({ options, value, onChange, placeholder, id }) => {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  const selected = options.find((o) => o.value === value);

  const close = useCallback(() => setOpen(false), []);

  useEffect(() => {
    if (!open) return;
    const handleClick = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) close();
    };
    document.addEventListener('mousedown', handleClick);
    return () => document.removeEventListener('mousedown', handleClick);
  }, [open, close]);

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Escape') { close(); return; }
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault();
      setOpen((prev) => !prev);
      return;
    }
    if (!open) return;
    const idx = options.findIndex((o) => o.value === value);
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      const next = Math.min(idx + 1, options.length - 1);
      onChange(options[next].value);
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      const prev = Math.max(idx - 1, 0);
      onChange(options[prev].value);
    }
  };

  return (
    <div className="custom-select" ref={ref}>
      <button
        type="button"
        id={id}
        className={`custom-select__trigger ${open ? 'custom-select__trigger--open' : ''}`}
        onClick={() => setOpen((prev) => !prev)}
        onKeyDown={handleKeyDown}
        aria-haspopup="listbox"
        aria-expanded={open}
      >
        <span className={selected ? '' : 'custom-select__placeholder'}>
          {selected ? selected.label : placeholder ?? 'Select...'}
        </span>
        <Icon name="chevronRight" size={16} className={`custom-select__chevron ${open ? 'custom-select__chevron--open' : ''}`} />
      </button>

      {open && (
        <ul className="custom-select__menu" role="listbox">
          {options.map((opt) => (
            <li
              key={opt.value}
              role="option"
              aria-selected={opt.value === value}
              className={`custom-select__option ${opt.value === value ? 'custom-select__option--active' : ''}`}
              onClick={() => { onChange(opt.value); close(); }}
            >
              {opt.label}
              {opt.value === value && (
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                  <polyline points="20 6 9 17 4 12" />
                </svg>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
};

export default CustomSelect;
