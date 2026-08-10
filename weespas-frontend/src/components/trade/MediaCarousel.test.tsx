import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import MediaCarousel from './MediaCarousel';

const IMG = '/uploads/trade/images/a.png';
const IMG2 = '/uploads/trade/images/b.png';
const VID = '/uploads/trade/videos/c.mp4';

describe('MediaCarousel', () => {
  it('renders a single image with no arrows or dots', () => {
    render(<MediaCarousel urls={[IMG]} title="Sukuma" />);
    expect(screen.getByRole('img')).toBeInTheDocument();
    expect(screen.queryByTestId('carousel-next')).toBeNull();
    expect(screen.queryByTestId('carousel-counter')).toBeNull();
  });

  it('shows arrows, a counter and dots for multiple media', () => {
    render(<MediaCarousel urls={[IMG, IMG2, VID]} title="Sukuma" />);
    expect(screen.getByTestId('carousel-next')).toBeInTheDocument();
    expect(screen.getByTestId('carousel-counter').textContent).toBe('1/3');
    expect(screen.getAllByRole('tab')).toHaveLength(3);
  });

  it('advances on next and wraps around', () => {
    render(<MediaCarousel urls={[IMG, IMG2]} title="Sukuma" />);
    expect(screen.getByTestId('carousel-counter').textContent).toBe('1/2');
    fireEvent.click(screen.getByTestId('carousel-next'));
    expect(screen.getByTestId('carousel-counter').textContent).toBe('2/2');
    // Wrap forward back to the first.
    fireEvent.click(screen.getByTestId('carousel-next'));
    expect(screen.getByTestId('carousel-counter').textContent).toBe('1/2');
    // Wrap backward to the last.
    fireEvent.click(screen.getByTestId('carousel-prev'));
    expect(screen.getByTestId('carousel-counter').textContent).toBe('2/2');
  });

  it('renders a <video> for a video slide', () => {
    render(<MediaCarousel urls={[VID, IMG]} title="Reel" />);
    expect(screen.getByTestId('carousel-video')).toBeInTheDocument();
    // Move to the image slide → no video.
    fireEvent.click(screen.getByTestId('carousel-next'));
    expect(screen.queryByTestId('carousel-video')).toBeNull();
    expect(screen.getByRole('img')).toBeInTheDocument();
  });

  it('jumps to a slide when its dot is clicked', () => {
    render(<MediaCarousel urls={[IMG, IMG2, VID]} title="Sukuma" />);
    const dots = screen.getAllByRole('tab');
    fireEvent.click(dots[2]);
    expect(screen.getByTestId('carousel-counter').textContent).toBe('3/3');
    expect(screen.getByTestId('carousel-video')).toBeInTheDocument();
  });

  it('calls onSelect when the stage is tapped (image slide)', () => {
    const onSelect = vi.fn();
    render(<MediaCarousel urls={[IMG]} title="Sukuma" onSelect={onSelect} />);
    fireEvent.click(screen.getByRole('img'));
    expect(onSelect).toHaveBeenCalledOnce();
  });

  it('renders nothing for an empty url list', () => {
    const { container } = render(<MediaCarousel urls={[]} title="Sukuma" />);
    expect(container.firstChild).toBeNull();
  });

  // #2/#3 — the box takes the first slide's TRUE aspect ratio (portrait tall / landscape wide),
  // published as the `--media-ratio` custom property. jsdom doesn't decode images, so we stub the
  // natural dimensions on the load event to simulate the browser measuring the decoded bitmap.
  const fireImgLoad = (img: HTMLImageElement, w: number, h: number) => {
    Object.defineProperty(img, 'naturalWidth', { value: w, configurable: true });
    Object.defineProperty(img, 'naturalHeight', { value: h, configurable: true });
    fireEvent.load(img);
  };

  it('locks the box to the FIRST slide\'s true aspect ratio on load', () => {
    render(<MediaCarousel urls={[IMG]} title="Sukuma" />);
    const box = screen.getByTestId('media-carousel');
    // Unmeasured ⇒ no inline ratio (CSS fallback of 1 applies).
    expect(box.style.getPropertyValue('--media-ratio')).toBe('');
    // A 1200×1600 portrait ⇒ ratio 0.75.
    fireImgLoad(screen.getByRole('img') as HTMLImageElement, 1200, 1600);
    expect(box.style.getPropertyValue('--media-ratio')).toBe('0.75');
  });

  it('does NOT let a later slide overwrite the first slide\'s locked ratio', () => {
    render(<MediaCarousel urls={[IMG, IMG2]} title="Sukuma" />);
    const box = screen.getByTestId('media-carousel');
    // Measure slide 1 (landscape 1600×900 ⇒ ~1.778).
    fireImgLoad(screen.getByRole('img') as HTMLImageElement, 1600, 900);
    const locked = box.style.getPropertyValue('--media-ratio');
    expect(Number(locked)).toBeCloseTo(1.778, 2);
    // Advance to slide 2 and fire its load with a DIFFERENT (portrait) ratio — must not re-measure.
    fireEvent.click(screen.getByTestId('carousel-next'));
    fireImgLoad(screen.getByRole('img') as HTMLImageElement, 1080, 1920);
    expect(box.style.getPropertyValue('--media-ratio')).toBe(locked);
  });

  it('ignores a degenerate (zero-dimension) first image', () => {
    render(<MediaCarousel urls={[IMG]} title="Sukuma" />);
    const box = screen.getByTestId('media-carousel');
    fireImgLoad(screen.getByRole('img') as HTMLImageElement, 0, 0);
    expect(box.style.getPropertyValue('--media-ratio')).toBe('');
  });
});
