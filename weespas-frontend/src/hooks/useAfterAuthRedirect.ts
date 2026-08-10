import { useCallback } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { resumeInsarAfterLogin } from '../api/insar';

const TOKEN_KEY = 'weespas_token';

/**
 * Shared post-authentication redirect for the Login and Register pages.
 *
 * Most sign-ins land the user on the home page. But when they arrived via a "Risk Map" /
 * "View on risk map" click while signed out, the URL carries `?next=insar` (InSAR is free
 * but login-required, see api/insar.ts): in that case we open InSAR deep-linked in a new
 * tab and leave this tab on the home page.
 *
 * The fresh token is read from localStorage, not auth context: persistSession() writes it
 * synchronously during login, whereas the context value is still stale inside the same
 * handler tick (React state updates async).
 */
export function useAfterAuthRedirect(): () => Promise<void> {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const next = searchParams.get('next');
  const nextListing = searchParams.get('listing');

  return useCallback(async () => {
    const freshToken = localStorage.getItem(TOKEN_KEY);
    if (freshToken) {
      await resumeInsarAfterLogin(freshToken, next, nextListing);
    }
    navigate('/');
  }, [navigate, next, nextListing]);
}
