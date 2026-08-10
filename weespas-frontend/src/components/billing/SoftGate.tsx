// src/components/billing/SoftGate.tsx
//
// The mount-once driver for the §8 soft-gate. Watches the signed-in user's policy
// verdict (usePolicyStatus) and shows ProScaleModal the FIRST time they cross into
// 'metered'. It fires at most once per threshold-crossing per user: a localStorage
// flag keyed on user id remembers we've shown it, so it never nags on every load. If
// the user later DROPS below the threshold and crosses again, the flag is cleared on
// the way down, so a genuine new crossing can prompt afresh.
//
// Renders nothing for anonymous users, free users, or once dismissed — so it's inert
// for everyone except an authenticated professional-scale account.
import React, { useEffect, useState } from 'react';
import { useAuth } from '../../context/AuthContext';
import { usePolicyStatus } from '../../hooks/usePolicyStatus';
import ProScaleModal from './ProScaleModal';

const SEEN_PREFIX = 'weespas_proscale_seen_';

function seenKey(userId: string): string {
  return `${SEEN_PREFIX}${userId}`;
}

const SoftGate: React.FC = () => {
  const { user, isAuthenticated } = useAuth();
  const { data } = usePolicyStatus();
  const [open, setOpen] = useState(false);

  const metered = data?.decision === 'metered';

  useEffect(() => {
    if (!isAuthenticated || !user) return;
    const key = seenKey(user.id);
    if (metered) {
      // First crossing for this user → show once and remember.
      if (!localStorage.getItem(key)) {
        localStorage.setItem(key, '1');
        setOpen(true);
      }
    } else {
      // Below threshold: clear the flag so a future genuine crossing prompts again.
      localStorage.removeItem(key);
    }
  }, [metered, isAuthenticated, user]);

  if (!open) return null;
  return (
    <ProScaleModal
      user={user}
      signals={data?.signals}
      onClose={() => setOpen(false)}
    />
  );
};

export default SoftGate;
