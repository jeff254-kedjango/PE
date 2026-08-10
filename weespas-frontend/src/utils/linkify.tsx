// Tiny linkifier for the agent bio (and any other short user-supplied text).
//
// Why a hand-rolled tokenizer instead of a library:
// - The bio is capped at 500 chars; we only need to recognize URLs and
//   phone numbers. A library like linkify-it (~12 KB gzipped + react-linkify
//   wrapper) is overkill for ~30 lines of regex.
// - The pattern is constructed once at module load and re-used across every
//   render. O(n) over bio length per render; at 500 chars that's a handful
//   of microseconds — well under one frame even on the low-end Android
//   devices the platform targets across East Africa.
//
// Patterns (alternation, non-overlapping):
//   1. http(s)://...                       → external link, new tab
//   2. www.example.com/...                 → external link, prefixed with https://
//   3. +?\d[\d\s().-]{8,18}\d              → tel: link
//
// The 8…18-digit threshold is chosen so it matches international East-African
// numbers like `+254 712 345 678`, `+256 7xx xxx xxx`, `+255`, `+250`, `+257`,
// without false-matching short numerics like postal codes, plot numbers,
// or property prices (which are typically 5–7 digits with no separators).
import React from 'react';

const PATTERN = /(https?:\/\/[^\s<>"]+|www\.[^\s<>"]+|\+?\d[\d\s().-]{8,18}\d)/g;

const isPhone = (s: string) => /^\+?\d/.test(s);

export function linkify(text: string | null | undefined): React.ReactNode[] {
  if (!text) return [];
  const out: React.ReactNode[] = [];
  let last = 0;
  let key = 0;

  // `replace` with a callback walks the string once and lets us pull out
  // each match plus its offset — cheaper than `matchAll` + slicing in a loop.
  text.replace(PATTERN, (match: string, _g: string, offset: number) => {
    if (offset > last) out.push(text.slice(last, offset));
    if (isPhone(match)) {
      // Strip everything but digits and the leading + so the tel: target
      // is a clean dial string. Display the original (formatted) text.
      const tel = match.replace(/[^\d+]/g, '');
      out.push(
        <a key={key++} href={`tel:${tel}`} className="bio-link bio-link--tel">
          {match}
        </a>,
      );
    } else {
      const href = match.startsWith('http') ? match : `https://${match}`;
      // target=_blank + rel=noopener noreferrer: standard hardening so
      // the new tab can't reach window.opener back into our SPA.
      out.push(
        <a
          key={key++}
          href={href}
          target="_blank"
          rel="noopener noreferrer"
          className="bio-link"
        >
          {match}
        </a>,
      );
    }
    last = offset + match.length;
    return match;
  });

  if (last < text.length) out.push(text.slice(last));
  return out;
}
