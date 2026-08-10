/**
 * Insert `insertText` into the current value of a text field at its caret/selection, replacing any
 * selected range. Pure given the element's selection state — returns the new string plus where the
 * caret should land (just after the inserted text) so the caller can restore focus + cursor.
 *
 * Used by the emoji palette: clicking an emoji drops it where the user was typing rather than
 * always appending at the end. Works for both <input> and <textarea>.
 */
export interface InsertResult {
  next: string;
  caret: number;
}

export function insertAtCursor(
  el: HTMLInputElement | HTMLTextAreaElement | null,
  current: string,
  insertText: string,
): InsertResult {
  // No element (or no selection info) → append at the end, caret after the insert.
  if (el == null || el.selectionStart == null) {
    const next = current + insertText;
    return { next, caret: next.length };
  }
  const start = el.selectionStart;
  // selectionEnd can be null in theory; fall back to start (a zero-width caret).
  const end = el.selectionEnd ?? start;
  const next = current.slice(0, start) + insertText + current.slice(end);
  return { next, caret: start + insertText.length };
}
