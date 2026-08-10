/* ==========================================================================
   PROPERTY DETAILS BY ID — wrapper that resolves a propertyId to the full
   Property record (including the images[] gallery) before rendering
   PropertyDetails.

   Why this exists:
   - Listing endpoints (`/properties/related`, `/properties/agents/:id/listings`,
     etc.) return `PropertyListResponse` — `main_image` only, no `images[]`.
   - PropertyDetails renders an image carousel from `images[]`, so opening it
     with a listing-shaped Property only ever shows one picture.
   - Calling `/properties/:id` returns the full `PropertyResponse` with the
     gallery, but doing that ad-hoc in every page creates duplicate loading
     state and inconsistent UX.

   Performance:
   - Reuses `usePropertyDetails(id)` so we share the SAME react-query cache
     key (`['property', id]`) as the rest of the app. If the detail was
     already fetched (e.g. user opened it earlier, or HomePage prefetched
     it) we render instantly with ZERO network — `staleTime: 2 min`.
   - The wrapper passes a `fallbackProperty` (the listing-shaped record the
     caller already has) so PropertyDetails appears INSTANTLY on click and
     transparently upgrades to the full gallery as soon as the detail
     resolves. No spinner gates the open.
   - No extra re-renders: we only swap `property` from fallback → full once,
     when the query settles. PropertyDetails is memoization-friendly.
   ========================================================================== */

import React, { useMemo } from 'react';
import PropertyDetails from './PropertyDetails';
import { usePropertyDetails } from '../../hooks/usePropertyDetails';
import type { Property } from '../../types/propertyApi';

interface Props {
  propertyId: string;
  /**
   * Listing-shaped Property the caller already has (e.g. the card the user
   * clicked). Lets us mount PropertyDetails instantly while the full detail
   * resolves — no spinner gate on click. Optional: when omitted, the panel
   * waits for the detail to load before mounting.
   */
  fallbackProperty?: Property | null;
  onClose: () => void;
}

const PropertyDetailsById: React.FC<Props> = ({ propertyId, fallbackProperty, onClose }) => {
  const { data: fullProperty } = usePropertyDetails(propertyId);

  // Prefer the full record once it lands; otherwise fall back to whatever
  // the caller had (a listing-shape with just `main_image`). The memo keeps
  // the prop identity stable so PropertyDetails doesn't re-render every tick.
  const property = useMemo<Property | null>(
    () => fullProperty ?? fallbackProperty ?? null,
    [fullProperty, fallbackProperty],
  );

  if (!property) return null;

  return <PropertyDetails property={property} onClose={onClose} />;
};

export default React.memo(PropertyDetailsById);
