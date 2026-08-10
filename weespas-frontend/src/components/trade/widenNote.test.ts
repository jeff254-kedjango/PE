import { describe, it, expect } from 'vitest';
import { widenNoteText } from './widenNote';

describe('widenNoteText', () => {
  it('returns null when not widened (nothing honest to say)', () => {
    expect(widenNoteText(false, 4200, 0)).toBeNull();
    expect(widenNoteText(false, 4200, 3)).toBeNull();
  });

  it('returns null when widened but distance is missing or non-positive', () => {
    expect(widenNoteText(true, null, 0)).toBeNull();
    expect(widenNoteText(true, 0, 0)).toBeNull();
    expect(widenNoteText(true, -5, 0)).toBeNull();
  });

  it('EMPTY branch (immediateCount 0): says nothing is in the immediate area', () => {
    const note = widenNoteText(true, 4300, 0);
    expect(note).toBe('Nothing selling in your immediate area — closest shops are within 5 km.');
  });

  it('SPARSE branch (immediateCount > 0): never claims emptiness', () => {
    const note = widenNoteText(true, 300, 2);
    expect(note).toBe('Only a few sellers nearby — also showing shops within 1 km.');
    expect(note).not.toMatch(/nothing/i);
  });

  it('phrases distance as an UPPER BOUND — rounds metres UP to whole km, min 1', () => {
    expect(widenNoteText(true, 1, 0)).toContain('within 1 km');       // <1 km still floors at 1
    expect(widenNoteText(true, 1001, 0)).toContain('within 2 km');    // ceils up, never understates
    expect(widenNoteText(true, 5000, 0)).toContain('within 5 km');    // exact km
  });

  it('defaults immediateCount to 0 (empty branch) when omitted — back-compatible', () => {
    expect(widenNoteText(true, 4300)).toMatch(/nothing selling in your immediate area/i);
  });

  it('never claims delivery on either branch (the platform has no fulfilment)', () => {
    expect(widenNoteText(true, 4300, 0)).not.toMatch(/deliver/i);
    expect(widenNoteText(true, 4300, 5)).not.toMatch(/deliver/i);
  });
});
