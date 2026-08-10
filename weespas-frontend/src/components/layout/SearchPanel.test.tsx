import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';

// SearchPanel fetches the category list on mount; keep it off the network and deterministic.
vi.mock('../../api/properties', () => ({ fetchCategories: vi.fn().mockResolvedValue([]) }));

import SearchPanel from './SearchPanel';
import type { PropertyFilterParams } from '../../types/propertyApi';

/**
 * Guards the filter-header contract: "Search My Location" is promoted OUT of the popover form and
 * sits to the LEFT of the Filters trigger, so the row reads Title …… [Search My Location][Filters].
 */
function renderPanel(filters: PropertyFilterParams = {}, onUseLocation = vi.fn()) {
  const onChange = vi.fn();
  const onSearch = vi.fn();
  render(
    <SearchPanel
      filters={filters}
      onChange={onChange}
      onSearch={onSearch}
      onUseLocation={onUseLocation}
    />,
  );
  return { onChange, onSearch, onUseLocation };
}

const locateBtn = () => screen.getByTestId('search-locate');
const filtersBtn = () => screen.getByTestId('search-panel-open');

// Braced body on purpose: a concise arrow would *return* VitestUtils, which TS reads as a bogus
// cleanup callback (TS2322).
beforeEach(() => {
  vi.clearAllMocks();
});

describe('SearchPanel — relocated "Search My Location" control', () => {
  it('renders the locate button OUTSIDE the popover anchor and BEFORE the Filters trigger', () => {
    renderPanel();
    // Not nested inside the popover anchor — otherwise clicking it would hit the outside-click scope.
    expect(locateBtn().closest('.search-filter')).toBeNull();
    // DOM order is the visual order: locate precedes Filters in the same bar.
    expect(locateBtn().compareDocumentPosition(filtersBtn()))
      .toBe(Node.DOCUMENT_POSITION_FOLLOWING);
    expect(locateBtn().parentElement).toBe(filtersBtn().closest('.search-filter')!.parentElement);
  });

  it('is labelled "Search My Location" and keeps the crosshair icon', () => {
    renderPanel();
    expect(locateBtn()).toHaveTextContent('Search My Location');
    expect(locateBtn().querySelector('svg')).not.toBeNull();
  });

  it('requests geolocation on click, then shows the locating state', async () => {
    const { onUseLocation } = renderPanel();
    fireEvent.click(locateBtn());
    expect(onUseLocation).toHaveBeenCalledTimes(1);
    await waitFor(() => expect(locateBtn()).toHaveTextContent('Locating…'));
    expect(locateBtn()).toBeDisabled();
  });

  it('reflects an acquired fix as "Location set"', () => {
    renderPanel({ latitude: -1.29, longitude: 36.82 });
    expect(locateBtn()).toHaveTextContent('Location set');
    expect(locateBtn().className).toContain('located');
  });

  it('no longer renders a location control inside the filter form', () => {
    renderPanel();
    fireEvent.click(filtersBtn());
    const form = document.querySelector('.search-panel__body')!;
    expect(form).not.toBeNull();
    expect(form.textContent).not.toMatch(/location/i);
  });

  it('counts an acquired fix toward the active-filter badge', () => {
    renderPanel({ latitude: -1.29, longitude: 36.82 });
    expect(filtersBtn().querySelector('.search-filter__trigger-badge')).toHaveTextContent('1');
  });
});
