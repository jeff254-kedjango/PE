// Engagement mutations for a listing post — save (bookmark) + inquiry ("is this available?").
//
// Both are buyer actions on the commerce bridge session. `useToggleSave` returns the server's new
// {saved, save_count} so a card flips its UI without a refetch (the feed item carries save_count
// but not the caller's own saved-state, so the card seeds from the count and tracks the toggle
// result locally). `useCreateInquiry` fires the private seller-inbox message — distinct from a
// public comment.
import { useMutation } from '@tanstack/react-query';
import {
  toggleSave,
  createInquiry,
  type SaveToggleResult,
  type InquiryResult,
  type CommerceSession,
} from '../api/commerce';

export function useToggleSave(session: CommerceSession | null, listingId: string) {
  return useMutation<SaveToggleResult, Error, void>({
    mutationFn: () => toggleSave(session!, listingId),
  });
}

export function useCreateInquiry(session: CommerceSession | null, listingId: string) {
  return useMutation<InquiryResult, Error, string | undefined>({
    mutationFn: (message?: string) => createInquiry(session!, listingId, message),
  });
}
