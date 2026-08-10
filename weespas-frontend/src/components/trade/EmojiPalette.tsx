import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import './EmojiPalette.css';

/**
 * A tiny, dependency-free emoji picker: a fixed grid of common native-unicode emoji. Clicking one
 * calls `onPick(emoji)`; the popover closes on outside-click or Escape. Deliberately NOT a full
 * searchable picker (no extra bundle) — just the everyday set people reach for in a chat/feed.
 *
 * Positioning: when an `anchorRef` is given, the palette is rendered through a PORTAL to
 * document.body with `position: fixed`, placed just below the anchor (flipping above when there's
 * no room). This is what lets it escape an ancestor's `overflow: hidden` — the comment thread sits
 * inside `.product-card { overflow: hidden }`, which would otherwise CLIP the palette inside the
 * card (a z-index can't lift an element out of an overflow clip). Without an anchorRef it falls
 * back to a plain absolutely-positioned popover anchored by its relative parent.
 */
export const EMOJIS: readonly string[] = [
  // faces / emotion (most-used set)
  '😂', '🤣', '❤️', '😍', '🥰', '😊', '😭', '😘',
  '🥺', '😅', '😁', '🙂', '😉', '😎', '😢', '😆',
  '😋', '😌', '😔', '😏', '🤔', '🤗', '🤩', '🥳',
  '😴', '😜', '😒', '🙃', '😄', '😀', '😇', '🤤',
  '😬', '😱', '😤', '😡', '🤯', '🥹', '😳', '🤫',
  // hands / gestures
  '👍', '👎', '🙏', '👏', '🙌', '🤝', '💪', '👌',
  '✌️', '🤞', '👋', '🤙', '👇', '👀', '🫶', '🤲',
  // hearts / sparkle
  '❤️‍🔥', '🧡', '💛', '💚', '💙', '💜', '🖤', '💯',
  '🔥', '✨', '⭐', '🎉', '🎊', '💕', '💖', '😻',
  // everyday objects / symbols (buy-sell feed)
  '✅', '❌', '💰', '🛒', '📦', '🏠', '🚗', '⚡',
];

interface EmojiPaletteProps {
  onPick: (emoji: string) => void;
  onClose: () => void;
  /** When set, the palette portals to <body> and positions itself (fixed) relative to this element,
   *  escaping any ancestor `overflow: hidden`. Pass the emoji TRIGGER button's ref. */
  anchorRef?: React.RefObject<HTMLElement>;
}

const MARGIN = 6;       // gap between the trigger and the palette
const VIEWPORT_PAD = 8; // keep the palette this far from the viewport edges

export default function EmojiPalette({ onPick, onClose, anchorRef }: EmojiPaletteProps) {
  const ref = useRef<HTMLDivElement>(null);
  const portaled = !!anchorRef;
  // Fixed-position coordinates when portaled; null until measured (rendered hidden to avoid a flash).
  const [pos, setPos] = useState<{ top: number; left: number } | null>(null);

  useEffect(() => {
    // Outside-click + Escape both dismiss. Pointerdown (not click) so picking an emoji — which
    // mutates focus — doesn't race the close handler. A pointerdown on the ANCHOR (the trigger
    // button) is ignored here so the button stays a clean toggle (its own onClick handles it).
    const onPointerDown = (e: PointerEvent) => {
      const target = e.target as Node;
      if (ref.current?.contains(target)) return;
      if (anchorRef?.current?.contains(target)) return;
      onClose();
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    document.addEventListener('pointerdown', onPointerDown);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('pointerdown', onPointerDown);
      document.removeEventListener('keydown', onKey);
    };
  }, [onClose, anchorRef]);

  // Position the portaled palette under (or above) the trigger, clamped to the viewport. Runs in a
  // layout effect so the measured size is available before paint; recomputed on scroll/resize so it
  // tracks the trigger while open.
  useLayoutEffect(() => {
    if (!portaled) return;
    const place = () => {
      const anchor = anchorRef?.current;
      const palette = ref.current;
      if (!anchor || !palette) return;
      const a = anchor.getBoundingClientRect();
      const p = palette.getBoundingClientRect();
      // Prefer below the trigger; flip above if there isn't room and there's more space up top.
      let top = a.bottom + MARGIN;
      if (top + p.height > window.innerHeight - VIEWPORT_PAD && a.top - p.height - MARGIN > VIEWPORT_PAD) {
        top = a.top - p.height - MARGIN;
      }
      // Left-align to the trigger, clamped so the palette never runs off either edge.
      let left = a.left;
      left = Math.min(left, window.innerWidth - p.width - VIEWPORT_PAD);
      left = Math.max(VIEWPORT_PAD, left);
      setPos({ top, left });
    };
    place();
    window.addEventListener('scroll', place, true); // capture: catch scrolls on any ancestor
    window.addEventListener('resize', place);
    return () => {
      window.removeEventListener('scroll', place, true);
      window.removeEventListener('resize', place);
    };
  }, [portaled, anchorRef]);

  const palette = (
    <div
      ref={ref}
      className={`emoji-palette${portaled ? ' emoji-palette--fixed' : ''}`}
      role="dialog"
      aria-label="Pick an emoji"
      data-testid="emoji-palette"
      style={
        portaled
          ? { top: pos?.top ?? 0, left: pos?.left ?? 0, visibility: pos ? 'visible' : 'hidden' }
          : undefined
      }
    >
      {EMOJIS.map((e) => (
        <button
          key={e}
          type="button"
          className="emoji-palette__item"
          // Keep the textarea focused: prevent the button from stealing focus on mousedown so the
          // caret/selection survives for insertAtCursor.
          onMouseDown={(ev) => ev.preventDefault()}
          onClick={() => onPick(e)}
          aria-label={`Insert ${e}`}
        >
          {e}
        </button>
      ))}
    </div>
  );

  return portaled ? createPortal(palette, document.body) : palette;
}
