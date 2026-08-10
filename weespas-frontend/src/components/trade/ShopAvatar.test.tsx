import { describe, it, expect } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import ShopAvatar from './ShopAvatar';

describe('ShopAvatar', () => {
  it('renders the image when a url is given (relative url resolved to the media host)', () => {
    render(<ShopAvatar url="/uploads/trade/images/a.jpg" name="Mama Njeri" />);
    const img = screen.getByTestId('shop-avatar-img') as HTMLImageElement;
    expect(img.tagName).toBe('IMG');
    expect(img.src).toContain('/uploads/trade/images/a.jpg');
    expect(screen.queryByTestId('shop-avatar-initial')).toBeNull();
  });

  it('falls back to the initial of the name when no url is given', () => {
    render(<ShopAvatar url={null} name="Mama Njeri" />);
    const initial = screen.getByTestId('shop-avatar-initial');
    expect(initial.textContent).toBe('M');
    expect(screen.queryByTestId('shop-avatar-img')).toBeNull();
  });

  it('shows a neutral glyph when there is neither a url nor a name', () => {
    render(<ShopAvatar url={null} name={null} />);
    expect(screen.getByTestId('shop-avatar-initial').textContent).toBe('•');
  });

  it('falls back to initials when the image fails to load (no empty hole)', () => {
    render(<ShopAvatar url="/uploads/trade/images/missing.jpg" name="Duka" />);
    fireEvent.error(screen.getByTestId('shop-avatar-img'));
    expect(screen.getByTestId('shop-avatar-initial').textContent).toBe('D');
    expect(screen.queryByTestId('shop-avatar-img')).toBeNull();
  });

  it('passes the caller sizing class through to both variants', () => {
    const { rerender } = render(<ShopAvatar url="/uploads/x.jpg" name="X" className="product-card__avatar" />);
    expect(screen.getByTestId('shop-avatar-img').className).toContain('product-card__avatar');
    rerender(<ShopAvatar url={null} name="X" className="product-card__avatar" />);
    expect(screen.getByTestId('shop-avatar-initial').className).toContain('product-card__avatar');
  });
});
