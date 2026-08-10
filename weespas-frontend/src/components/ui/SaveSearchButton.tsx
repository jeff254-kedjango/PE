// SaveSearchButton — Phase 3 entry point on the home filter bar.
//
// Hidden when the user is signed out (saved searches are user-scoped).
// Disabled when the current filter set is effectively empty so we don't
// litter the panel with "Latest near me" rows that aren't real searches.
//
// Performance: the button itself ships ~1KB; React Query handles cache
// invalidation on success so the PreferencesPanel list refreshes the
// next time it mounts without a refetch on this page.
import { useCallback, useState } from 'react';
import { useAuth } from '../../context/AuthContext';
import { useCreateSavedSearch } from '../../hooks/useSavedSearches';
import { useToast } from '../../context/ToastContext';
import Icon from './Icon';
import './SaveSearchButton.css';

interface Props {
  filters: Record<string, unknown>;
  /** True when the user has actually applied a search (vs. the default landing view). */
  searchApplied: boolean;
}

const SKIP_KEYS = new Set(['skip', 'limit', 'sort_by', 'sort_order']);

function isMeaningful(filters: Record<string, unknown>): boolean {
  for (const [k, v] of Object.entries(filters)) {
    if (SKIP_KEYS.has(k)) continue;
    if (v === undefined || v === null || v === '') continue;
    if (k === 'category' && v === 'all') continue;
    if (k === 'radius' && v === 10) continue;
    return true;
  }
  return false;
}

export default function SaveSearchButton({ filters, searchApplied }: Props) {
  const { token } = useAuth();
  const { toast } = useToast();
  const save = useCreateSavedSearch();
  const [open, setOpen] = useState(false);
  const [name, setName] = useState('');

  const canSave = !!token && searchApplied && isMeaningful(filters);

  const handleSubmit = useCallback(async () => {
    const trimmed = name.trim();
    if (!trimmed) return;
    try {
      await save.mutateAsync({ name: trimmed.slice(0, 80), filters });
      toast.success(`Saved “${trimmed}”`);
      setOpen(false);
      setName('');
    } catch (e: any) {
      toast.error(e?.message ?? 'Could not save search');
    }
  }, [name, filters, save, toast]);

  if (!canSave) return null;

  return (
    <>
      <button
        type="button"
        className="save-search-btn"
        onClick={() => setOpen(true)}
        title="Save this search to your profile"
        aria-label="Save current search"
      >
        <Icon name="search" size={14} />
        <span>Save search</span>
      </button>

      {open && (
        <div
          className="save-search-modal"
          role="dialog"
          aria-modal="true"
          onClick={(e) => {
            if (e.target === e.currentTarget) setOpen(false);
          }}
        >
          <div className="save-search-modal__inner">
            <h3>Name this search</h3>
            <input
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="e.g. 3-bed in Karen under 80M"
              maxLength={80}
              autoFocus
              onKeyDown={(e) => {
                if (e.key === 'Enter') handleSubmit();
                if (e.key === 'Escape') setOpen(false);
              }}
            />
            <div className="save-search-modal__actions">
              <button
                type="button"
                className="save-search-btn save-search-btn--ghost"
                onClick={() => setOpen(false)}
                disabled={save.isPending}
              >
                Cancel
              </button>
              <button
                type="button"
                className="save-search-btn save-search-btn--primary"
                onClick={handleSubmit}
                disabled={save.isPending || name.trim().length === 0}
              >
                {save.isPending ? 'Saving…' : 'Save'}
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
