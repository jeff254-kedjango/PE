// The signed-in seller's inquiry inbox — buyers' "is this available?" messages on their listings.
//
// Newest-first, keyset-paginated (one page here; the inbox UI shows the latest and can extend with
// the cursor later). useMarkInquiryRead invalidates this key so an item's unread styling + the
// unread badge update immediately after marking it read.
import { useQuery } from '@tanstack/react-query';
import { getMyInquiries, type CommerceSession, type InquiryPage } from '../api/commerce';

export const MY_INQUIRIES_KEY = ['commerce', 'inquiries', 'mine'] as const;

export function useMyInquiries(session: CommerceSession | null) {
  return useQuery<InquiryPage, Error>({
    queryKey: [...MY_INQUIRIES_KEY, session?.commerce_url],
    queryFn: () => getMyInquiries(session!),
    enabled: !!session,
    staleTime: 15_000,
    retry: 1,
  });
}
