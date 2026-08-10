import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { createRef } from 'react';
import EmojiPalette, { EMOJIS } from './EmojiPalette';

describe('EmojiPalette', () => {
  it('renders a grid of emoji and reports the picked one', () => {
    const onPick = vi.fn();
    render(<EmojiPalette onPick={onPick} onClose={() => {}} />);
    const buttons = screen.getAllByRole('button');
    expect(buttons.length).toBe(EMOJIS.length);
    fireEvent.click(buttons[0]);
    expect(onPick).toHaveBeenCalledWith(EMOJIS[0]);
  });

  it('closes on Escape', () => {
    const onClose = vi.fn();
    render(<EmojiPalette onPick={() => {}} onClose={onClose} />);
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('closes on an outside pointerdown but not an inside one', () => {
    const onClose = vi.fn();
    render(
      <div>
        <button type="button" data-testid="outside">x</button>
        <EmojiPalette onPick={() => {}} onClose={onClose} />
      </div>,
    );
    // Inside the palette → stays open.
    fireEvent.pointerDown(screen.getByTestId('emoji-palette'));
    expect(onClose).not.toHaveBeenCalled();
    // Outside → closes.
    fireEvent.pointerDown(screen.getByTestId('outside'));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  describe('portaled (anchorRef given)', () => {
    it('portals to <body> with the fixed variant and positions itself', () => {
      const anchorRef = createRef<HTMLButtonElement>();
      const { container } = render(
        <div>
          <button type="button" ref={anchorRef} data-testid="trigger">😊</button>
          <EmojiPalette onPick={() => {}} onClose={() => {}} anchorRef={anchorRef} />
        </div>,
      );
      const palette = screen.getByTestId('emoji-palette');
      // Rendered through the portal → it lives under <body>, NOT inside the component's container
      // (this is what lets it escape an ancestor `overflow: hidden`).
      expect(container.contains(palette)).toBe(false);
      expect(document.body.contains(palette)).toBe(true);
      expect(palette.className).toContain('emoji-palette--fixed');
    });

    it('ignores a pointerdown on the anchor so the trigger stays a clean toggle', () => {
      const onClose = vi.fn();
      const anchorRef = createRef<HTMLButtonElement>();
      render(
        <div>
          <button type="button" ref={anchorRef} data-testid="trigger">😊</button>
          <EmojiPalette onPick={() => {}} onClose={onClose} anchorRef={anchorRef} />
        </div>,
      );
      // A pointerdown on the trigger must NOT fire onClose (the button's own onClick owns the toggle);
      // otherwise close-then-reopen would race and the palette could never open.
      fireEvent.pointerDown(screen.getByTestId('trigger'));
      expect(onClose).not.toHaveBeenCalled();
    });
  });
});
