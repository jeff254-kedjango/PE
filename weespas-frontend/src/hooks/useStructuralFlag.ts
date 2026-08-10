// Structural-flag mutation hook for the certifier entry UI.
//
// Wraps createStructuralFlag in a React Query mutation. On success it invalidates the
// listing-risk query so the RiskPill re-reads the (possibly escalated) tier once the
// InSAR rebuild lands — and the latest-flag query so the form reflects what was just set.
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useAuth } from '../context/AuthContext';
import { createStructuralFlag, type FlagCreate, type FlagOut } from '../api/structuralFlags';

export function useCreateStructuralFlag(listingId?: string) {
  const { token } = useAuth();
  const qc = useQueryClient();
  return useMutation<FlagOut, Error, FlagCreate>({
    mutationFn: (body: FlagCreate) => createStructuralFlag(token!, body),
    onSuccess: (flag) => {
      if (listingId) qc.invalidateQueries({ queryKey: ['listingRisk', listingId] });
      qc.invalidateQueries({
        queryKey: ['structuralFlag', flag.aoi_code, flag.insar_building_id],
      });
    },
  });
}
