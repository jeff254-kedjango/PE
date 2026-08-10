import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, act } from '@testing-library/react';
import SellerModal from './SellerModal';

// SellerModal owns the shared backdrop-close mechanics. These tests pin the two guards added to kill
// the reported Boost "flicker" (open-then-instantly-close): a close must be a DELIBERATE press that
// both starts and ends on the overlay, AND must come after a short grace window (so the tail of the
// double-click that opened the modal can't dismiss it). See SellerModal.tsx.

function renderModal(onClose: () => void) {
  render(
    <SellerModal title="Boost reach" onClose={onClose}>
      <button type="button">a field inside the dialog</button>
    </SellerModal>,
  );
  // The portal renders to document.body; the overlay is the outermost div.
  return document.querySelector('.seller-modal__overlay') as HTMLElement;
}

// A full "click" in the DOM is mousedown → mouseup → click; the guard tracks mousedown origin, so
// tests must fire the mousedown on the intended target before the click.
function pressAndClick(target: HTMLElement, downTarget: HTMLElement = target) {
  fireEvent.mouseDown(downTarget);
  fireEvent.click(target);
}

describe('SellerModal backdrop-close guards', () => {
  beforeEach(() => { vi.useFakeTimers(); });
  afterEach(() => { vi.useRealTimers(); });

  // Advance past the OVERLAY_CLOSE_GRACE_MS (300ms) mount-ignore window.
  const armClose = () => act(() => { vi.advanceTimersByTime(350); });

  it('does NOT close on an overlay click during the grace window (the flicker case)', () => {
    const onClose = vi.fn();
    const overlay = renderModal(onClose);
    // No timer advance: still inside the grace window — a double-click tail landing here is ignored.
    pressAndClick(overlay);
    expect(onClose).not.toHaveBeenCalled();
  });

  it('DOES close on a deliberate overlay press after the grace window (intended behaviour kept)', () => {
    const onClose = vi.fn();
    const overlay = renderModal(onClose);
    armClose();
    pressAndClick(overlay);
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('does NOT close when the press STARTED inside the dialog and released on the overlay', () => {
    // Classic mis-dismissal: begin a text selection/drag inside the dialog, release over the backdrop.
    const onClose = vi.fn();
    const overlay = renderModal(onClose);
    armClose();
    const inner = screen.getByText('a field inside the dialog');
    pressAndClick(overlay, inner); // mousedown on inner, click bubbles to overlay
    expect(onClose).not.toHaveBeenCalled();
  });

  it('does NOT close when the click target is the dialog, not the overlay', () => {
    const onClose = vi.fn();
    renderModal(onClose);
    armClose();
    const dialog = document.querySelector('.seller-modal') as HTMLElement;
    fireEvent.mouseDown(dialog);
    fireEvent.click(dialog);
    expect(onClose).not.toHaveBeenCalled();
  });

  it('closes on Escape', () => {
    const onClose = vi.fn();
    renderModal(onClose);
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});
