import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import ListingTypeBadge from './ListingTypeBadge';

// Smoke test proving the RTL + jsdom pipeline renders a real component.
describe('ListingTypeBadge', () => {
  it('renders FOR SALE for sale listings', () => {
    render(<ListingTypeBadge type="sale" />);
    expect(screen.getByText('FOR SALE')).toBeInTheDocument();
  });

  it('renders FOR RENT for rent listings', () => {
    render(<ListingTypeBadge type="rent" />);
    expect(screen.getByText('FOR RENT')).toBeInTheDocument();
  });

  it('renders nothing without a type', () => {
    const { container } = render(<ListingTypeBadge type={undefined} />);
    expect(container).toBeEmptyDOMElement();
  });
});
