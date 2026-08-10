// Regression guard for a real bug: `--color-surface` was USED by the modal/dialog
// shells (RoleApplicationModal / DeletionRequestModal / ConfirmDeleteDialog — and the
// structural-flag form via role-app-modal) but was never DEFINED in variables.css.
// `background: var(--color-surface)` then resolved to nothing → a transparent panel
// over the translucent overlay → unreadable form text. These tests assert the token
// exists and is OPAQUE so that can never silently return.
import { describe, it, expect } from 'vitest';

// Read variables.css at test time. We can't use `import './variables.css?raw'`
// because the vitest config sets `css: false` (CSS is stubbed → empty string),
// and we can't `import 'node:fs'` normally because the app's tsconfig ships no
// @types/node and `tsc` type-checks test files. So we declare a minimal ambient
// type for the one fs call we need; vitest runs on Node, so it resolves at runtime.
declare function require(id: string): { readFileSync(p: string, enc: string): string };
const read = (p: string) => require('fs').readFileSync(p, 'utf8') as string;
const variablesCss = read('src/styles/variables.css');
const flagModalCss = read('src/components/property/StructuralFlagModal.css');

// Extract the body `{...}` of a CSS rule by exact selector (first match).
function ruleBody(css: string, selector: string): string | null {
  const i = css.indexOf(selector);
  if (i === -1) return null;
  const open = css.indexOf('{', i);
  const close = css.indexOf('}', open);
  if (open === -1 || close === -1) return null;
  return css.slice(open + 1, close);
}

function tokenValue(css: string, name: string): string | null {
  // Match `--name: <value>;` inside :root. Last definition wins (cascade).
  const re = new RegExp(`${name}\\s*:\\s*([^;]+);`, 'g');
  let m: RegExpExecArray | null;
  let last: string | null = null;
  while ((m = re.exec(css))) last = m[1].trim();
  return last;
}

describe('--color-surface design token', () => {
  it('is defined in variables.css', () => {
    expect(tokenValue(variablesCss, '--color-surface')).not.toBeNull();
  });

  it('is opaque (not transparent / not an alpha colour)', () => {
    const v = (tokenValue(variablesCss, '--color-surface') ?? '').toLowerCase();
    expect(v).not.toBe('transparent');
    // Reject rgba()/hsla() with a fractional or zero alpha, and 8-digit hex with alpha.
    expect(/rgba?\([^)]*,\s*0?\.\d+\s*\)/.test(v)).toBe(false);
    expect(/hsla?\([^)]*,\s*0?\.\d+\s*\)/.test(v)).toBe(false);
    expect(/^#[0-9a-f]{8}$/.test(v)).toBe(false);
    // A bare hex / named / rgb() colour is opaque — that's what we want.
    expect(v.length).toBeGreaterThan(0);
  });
});

// Regression for the "only left corners rounded" bug: the rounded panel must clip
// (overflow:hidden) and must NOT itself be the scroll container — otherwise the
// scrollbar squares off the top-right + bottom-right corners.
describe('.sf-modal corner-rounding contract', () => {
  it('panel has a border-radius and clips with overflow:hidden', () => {
    const body = ruleBody(flagModalCss, '.sf-modal {') ?? '';
    expect(/border-radius:\s*15px/.test(body)).toBe(true);
    expect(/overflow:\s*hidden/.test(body)).toBe(true);
  });

  it('the panel itself does NOT own a vertical scrollbar', () => {
    const body = ruleBody(flagModalCss, '.sf-modal {') ?? '';
    // overflow-y:auto on the rounded panel is exactly what caused the bug.
    expect(/overflow-y:\s*auto/.test(body)).toBe(false);
  });

  it('is self-contained — does not borrow .role-app-modal CSS from another chunk', () => {
    // The modal owns its overlay/panel/chrome; depending on .role-app-modal (only
    // loaded by the profile page's lazy chunk) left it unstyled on the property page.
    // Check for actual SELECTORS (ignore explanatory comments that name the old class).
    const noComments = flagModalCss.replace(/\/\*[\s\S]*?\*\//g, '');
    expect(/\.role-app-modal/.test(noComments)).toBe(false);
    expect(flagModalCss.includes('.sf-modal-overlay')).toBe(true);
  });

  it('the inner .sf-modal__scroll wrapper owns the scroll + padding', () => {
    const body = ruleBody(flagModalCss, '.sf-modal__scroll {') ?? '';
    expect(/overflow-y:\s*auto/.test(body)).toBe(true);
    expect(/padding:/.test(body)).toBe(true);
  });
});

// Regression for "buttons stuck together": the actions row must be SELF-CONTAINED
// (.sf-modal__actions with its own display:flex + gap), not borrowed from
// RoleApplicationModal.css which the property page never imports.
describe('.sf-modal__actions button-gap contract', () => {
  it('defines its own flex layout with a gap (independent of RoleApplicationModal.css)', () => {
    const body = ruleBody(flagModalCss, '.sf-modal__actions {') ?? '';
    expect(/display:\s*flex/.test(body)).toBe(true);
    expect(/gap:\s*var\(--space-/.test(body)).toBe(true);
  });

  it('does NOT depend on the unimported .role-app-modal__actions class', () => {
    // The old coupling that broke the gap on the property page.
    expect(flagModalCss.includes('role-app-modal__actions')).toBe(false);
  });
});
