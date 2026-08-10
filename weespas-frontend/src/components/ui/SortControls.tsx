import React from 'react';
import CustomSelect, { SelectOption } from './CustomSelect';
import './SortControls.css';

export interface SortOption {
  label: string;
  sort_by: 'price' | 'distance' | 'created_at';
  sort_order: 'asc' | 'desc';
}

const SORT_OPTIONS: SortOption[] = [
  { label: 'Nearest',          sort_by: 'distance',   sort_order: 'asc'  },
  { label: 'Price: Low\u2013High',  sort_by: 'price',      sort_order: 'asc'  },
  { label: 'Price: High\u2013Low',  sort_by: 'price',      sort_order: 'desc' },
  { label: 'Newest',           sort_by: 'created_at', sort_order: 'desc' },
];

const selectOptions: SelectOption[] = SORT_OPTIONS.map((opt) => ({
  label: opt.label,
  value: `${opt.sort_by}__${opt.sort_order}`,
}));

interface SortControlsProps {
  sortBy: string;
  sortOrder: string;
  onChange: (sort_by: SortOption['sort_by'], sort_order: SortOption['sort_order']) => void;
  resultCount: number;
}

const SortControls: React.FC<SortControlsProps> = ({ sortBy, sortOrder, onChange, resultCount }) => {
  const currentValue = `${sortBy}__${sortOrder}`;

  const handleChange = (value: string) => {
    const [by, order] = value.split('__') as [SortOption['sort_by'], SortOption['sort_order']];
    onChange(by, order);
  };

  return (
    <div className="sort-controls">
      <span className="sort-controls__count">
        {resultCount} listing{resultCount !== 1 ? 's' : ''}
      </span>
      <div className="sort-controls__select-wrap">
        <label className="sort-controls__label">Sort by</label>
        <CustomSelect
          options={selectOptions}
          value={currentValue}
          onChange={handleChange}
          id="sort-select"
        />
      </div>
    </div>
  );
};

export default SortControls;
