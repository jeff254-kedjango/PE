import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import FeedKindToggle, { laneToFeedKind } from './FeedKindToggle';

describe('FeedKindToggle', () => {
  it('marks the active lane as selected (aria) and shows all three labels', () => {
    render(<FeedKindToggle lane="shops" onChange={() => {}} />);
    expect(screen.getByTestId('kind-shops').getAttribute('aria-selected')).toBe('true');
    expect(screen.getByTestId('kind-clips').getAttribute('aria-selected')).toBe('false');
    expect(screen.getByTestId('kind-podcasts').getAttribute('aria-selected')).toBe('false');
    // icon + short word (the product requirement)
    expect(screen.getByText('Shops')).toBeTruthy();
    expect(screen.getByText('Clips')).toBeTruthy();
    expect(screen.getByText('Podcasts')).toBeTruthy();
  });

  it('fires onChange with the tapped lane', () => {
    const onChange = vi.fn();
    render(<FeedKindToggle lane="shops" onChange={onChange} />);
    fireEvent.click(screen.getByTestId('kind-clips'));
    expect(onChange).toHaveBeenCalledWith('clips');
    fireEvent.click(screen.getByTestId('kind-podcasts'));
    expect(onChange).toHaveBeenCalledWith('podcasts');
  });

  // The load-bearing guard. The commerce feed validates ?kind= against
  // FEED_KINDS = ('listings','videos') and 422s anything else, so 'podcasts' must map to null (no
  // request) rather than leak a third value onto the wire. If someone ever "helpfully" gives
  // podcasts a wire value, this test fails instead of the API.
  it('maps lanes to wire kinds, with no wire value for podcasts', () => {
    expect(laneToFeedKind('shops')).toBe('listings');
    expect(laneToFeedKind('clips')).toBe('videos');
    expect(laneToFeedKind('podcasts')).toBeNull();
  });
});
