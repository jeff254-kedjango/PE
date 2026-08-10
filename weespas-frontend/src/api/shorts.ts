import { fetchJson, API_BASE_URL } from './config';
import type { ListingType, PropertyCategory, PropertyImage } from '../types/propertyApi';

export interface ShortVideoEmbed {
  url: string;
  streaming_url?: string;
  thumbnail_url?: string;
  duration?: number;
}

export interface PropertyShort {
  id: string;
  title: string;
  price: number;
  currency: string;
  listing_type: ListingType;
  category: PropertyCategory;
  agent_name?: string;
  location_name: string;
  main_image?: PropertyImage;
  video: ShortVideoEmbed;
  is_featured: boolean;
  bedrooms?: number;
  bathrooms?: number;
}

export interface PaginatedShorts {
  total: number;
  skip: number;
  limit: number;
  items: PropertyShort[];
}

export async function fetchShortsFeed(
  params: { skip?: number; limit?: number; token?: string | null } = {},
): Promise<PaginatedShorts> {
  const q = new URLSearchParams();
  q.set('skip', String(params.skip ?? 0));
  q.set('limit', String(params.limit ?? 10));

  // credentials: 'include' is REQUIRED so the anon session cookie reaches the
  // backend — otherwise every anon collapses into the same global bucket.
  return fetchJson<PaginatedShorts>(`${API_BASE_URL}/properties/shorts?${q.toString()}`, {
    credentials: 'include',
    headers: params.token ? { Authorization: `Bearer ${params.token}` } : undefined,
  });
}
