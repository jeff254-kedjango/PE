import { describe, it, expect } from 'vitest';
import { insertAtCursor } from './insertAtCursor';

// A minimal stand-in for the bits of an input/textarea insertAtCursor reads.
function fakeField(selectionStart: number | null, selectionEnd: number | null) {
  return { selectionStart, selectionEnd } as HTMLInputElement;
}

describe('insertAtCursor', () => {
  it('appends at the end when there is no element', () => {
    const r = insertAtCursor(null, 'hi', '😀');
    expect(r.next).toBe('hi😀');
    expect(r.caret).toBe('hi😀'.length);
  });

  it('appends at the end when selection info is unavailable', () => {
    const r = insertAtCursor(fakeField(null, null), 'hi', '😀');
    expect(r.next).toBe('hi😀');
    expect(r.caret).toBe('hi😀'.length);
  });

  it('inserts at a collapsed caret in the middle', () => {
    // caret after "ab" in "abcd"
    const r = insertAtCursor(fakeField(2, 2), 'abcd', 'X');
    expect(r.next).toBe('abXcd');
    expect(r.caret).toBe(3);
  });

  it('replaces the selected range', () => {
    // select "bc" in "abcd"
    const r = insertAtCursor(fakeField(1, 3), 'abcd', 'X');
    expect(r.next).toBe('aXd');
    expect(r.caret).toBe(2);
  });

  it('treats a null selectionEnd as a collapsed caret at selectionStart', () => {
    const r = insertAtCursor(fakeField(2, null), 'abcd', 'X');
    expect(r.next).toBe('abXcd');
    expect(r.caret).toBe(3);
  });
});
