import { fetchJson, API_BASE_URL } from './config';
import { PaginatedResponse, Property, PropertyCategory, PropertyCreatePayload, PropertyUpdatePayload, PropertyFilterParams } from '../types/propertyApi';

function buildSearchParams(params: Record<string, any>) {
  const query = new URLSearchParams();

  Object.entries(params).forEach(([key, value]) => {
    if (value === undefined || value === null || value === '') return;
    query.set(key, String(value));
  });

  return query.toString();
}

const hasGeoQuery = (params: PropertyFilterParams) => {
  return params.latitude !== undefined && params.longitude !== undefined && params.radius !== undefined;
};

const hasAdvancedFilters = (params: PropertyFilterParams) => {
  return Boolean(
    params.listing_type ||
    (params.category && params.category !== 'all') ||
    params.min_price !== undefined ||
    params.max_price !== undefined ||
    params.engineer_certified !== undefined ||
    params.bedrooms !== undefined ||
    params.bathrooms !== undefined ||
    params.min_size !== undefined ||
    params.max_size !== undefined ||
    params.parking_spaces !== undefined ||
    params.year_built !== undefined ||
    params.is_featured !== undefined ||
    params.city ||
    params.county ||
    params.location_name ||
    params.sort_by ||
    params.sort_order ||
    params.query
  );
};

export async function fetchPropertyList(
  params: { skip?: number; limit?: number; token?: string | null } = {}
): Promise<PaginatedResponse<Property>> {
  const queryString = buildSearchParams({
    skip: params.skip ?? 0,
    limit: params.limit ?? 12
  });

  // credentials: 'include' is REQUIRED — without it the anon session cookie
  // never reaches the backend and every anonymous visitor collapses into the
  // same "anon:global" cache bucket (so ranking looks identical for everyone).
  return fetchJson<PaginatedResponse<Property>>(
    `${API_BASE_URL}/properties?${queryString}`,
    {
      credentials: 'include',
      headers: params.token ? { Authorization: `Bearer ${params.token}` } : undefined,
    }
  );
}

export async function fetchPropertyDetails(propertyId: string): Promise<Property> {
  return fetchJson<Property>(`${API_BASE_URL}/properties/${propertyId}`);
}

export async function fetchNearbyProperties(params: PropertyFilterParams): Promise<PaginatedResponse<Property>> {
  const queryString = buildSearchParams({
    latitude: params.latitude,
    longitude: params.longitude,
    radius: params.radius ?? 10,
    skip: params.skip ?? 0,
    limit: params.limit ?? 20
  });

  return fetchJson<PaginatedResponse<Property>>(
    `${API_BASE_URL}/properties/nearby?${queryString}`,
    { credentials: 'include' },
  );
}

export async function filterProperties(params: PropertyFilterParams): Promise<PaginatedResponse<Property>> {
  return fetchJson<PaginatedResponse<Property>>(`${API_BASE_URL}/properties/filter`, {
    method: 'POST',
    credentials: 'include',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      skip: params.skip ?? 0,
      limit: params.limit ?? 20,
      latitude: params.latitude,
      longitude: params.longitude,
      radius: params.radius,
      listing_type: params.listing_type,
      category: params.category === 'all' ? undefined : params.category,
      min_price: params.min_price,
      max_price: params.max_price,
      engineer_certified: params.engineer_certified,
      bedrooms: params.bedrooms,
      bathrooms: params.bathrooms,
      min_size: params.min_size,
      max_size: params.max_size,
      parking_spaces: params.parking_spaces,
      year_built: params.year_built,
      is_featured: params.is_featured,
      city: params.city || undefined,
      county: params.county || undefined,
      location_name: params.location_name || undefined,
      sort_by: params.sort_by,
      sort_order: params.sort_order,
      query: params.query
    })
  });
}

export async function searchProperties(query: string, skip = 0, limit = 20): Promise<PaginatedResponse<Property>> {
  const queryString = buildSearchParams({ q: query, skip, limit });
  return fetchJson<PaginatedResponse<Property>>(
    `${API_BASE_URL}/properties/search/query?${queryString}`,
    { credentials: 'include' },
  );
}

export interface FeaturedGeoOptions {
  latitude?: number;
  longitude?: number;
  radius?: number;
}

export async function fetchFeaturedProperties(
  limit?: number,
  geo?: FeaturedGeoOptions,
): Promise<Property[]> {
  // Omit `limit` to let the backend return the full active-featured set (its
  // default mirrors the cap). buildSearchParams drops undefined values.
  const queryString = buildSearchParams({
    limit,
    latitude: geo?.latitude,
    longitude: geo?.longitude,
    radius: geo?.radius,
  });
  return fetchJson<Property[]>(`${API_BASE_URL}/properties/featured?${queryString}`);
}

export async function fetchRelatedProperties(
  sourceIds: string[],
  limit = 12,
  excludeIds: string[] = [],
): Promise<Property[]> {
  return fetchJson<Property[]>(`${API_BASE_URL}/properties/related`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      source_ids: sourceIds,
      limit,
      exclude_ids: excludeIds,
    }),
  });
}

export async function fetchCategories(): Promise<PropertyCategory[]> {
  try {
    return await fetchJson<PropertyCategory[]>(`${API_BASE_URL}/properties/categories`);
  } catch (err) {
    console.error('Failed to fetch categories:', err);
    return [];
  }
}

export async function createProperty(
  token: string,
  data: PropertyCreatePayload
): Promise<Property> {
  return fetchJson<Property>(`${API_BASE_URL}/properties`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${token}`,
    },
    body: JSON.stringify(data),
  });
}

export async function updateProperty(
  token: string,
  propertyId: string,
  data: PropertyUpdatePayload
): Promise<Property> {
  return fetchJson<Property>(`${API_BASE_URL}/properties/${propertyId}`, {
    method: 'PUT',
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${token}`,
    },
    body: JSON.stringify(data),
  });
}

export async function deleteProperty(
  token: string,
  propertyId: string
): Promise<void> {
  const res = await fetch(`${API_BASE_URL}/properties/${propertyId}`, {
    method: 'DELETE',
    headers: { Authorization: `Bearer ${token}` },
    credentials: 'include',
  });

  if (res.status === 401) {
    localStorage.removeItem('weespas_token');
    localStorage.removeItem('weespas_user');
    window.location.href = '/login';
    throw new Error('Session expired. Please log in again.');
  }

  if (!res.ok) {
    const text = await res.text().catch(() => res.statusText);
    throw new Error(`Delete failed: ${res.status} ${text}`);
  }
}

export interface UploadedImage {
  id: string;
  url: string;
  thumbnail_url: string;
  original_filename: string;
  file_size: number;
  mime_type: string;
  order: number;
  is_main: boolean;
}

export interface UploadImagesResponse {
  uploaded: number;
  images: UploadedImage[];
}

export async function uploadPropertyImages(
  token: string,
  propertyId: string,
  files: File[]
): Promise<UploadImagesResponse> {
  const formData = new FormData();
  files.forEach((file) => formData.append('files', file));

  const res = await fetch(`${API_BASE_URL}/properties/${propertyId}/images`, {
    method: 'POST',
    headers: { Authorization: `Bearer ${token}` },
    credentials: 'include',
    body: formData,
  });

  if (!res.ok) {
    const text = await res.text().catch(() => res.statusText);
    throw new Error(`Upload failed: ${res.status} ${text}`);
  }

  return res.json();
}

export async function deletePropertyImage(
  token: string,
  propertyId: string,
  imageId: string
): Promise<void> {
  const res = await fetch(`${API_BASE_URL}/properties/${propertyId}/images/${imageId}`, {
    method: 'DELETE',
    headers: { Authorization: `Bearer ${token}` },
    credentials: 'include',
  });
  if (!res.ok) {
    const text = await res.text().catch(() => res.statusText);
    throw new Error(`Image delete failed: ${res.status} ${text}`);
  }
}

export async function deletePropertyVideo(
  token: string,
  propertyId: string,
  videoId: string
): Promise<void> {
  const res = await fetch(`${API_BASE_URL}/properties/${propertyId}/videos/${videoId}`, {
    method: 'DELETE',
    headers: { Authorization: `Bearer ${token}` },
    credentials: 'include',
  });
  if (!res.ok) {
    const text = await res.text().catch(() => res.statusText);
    throw new Error(`Video delete failed: ${res.status} ${text}`);
  }
}

export async function uploadPropertyVideo(
  token: string,
  propertyId: string,
  file: File
): Promise<{ id: string; url: string }> {
  const formData = new FormData();
  formData.append('file', file);

  const res = await fetch(`${API_BASE_URL}/properties/${propertyId}/videos`, {
    method: 'POST',
    headers: { Authorization: `Bearer ${token}` },
    credentials: 'include',
    body: formData,
  });

  if (!res.ok) {
    const text = await res.text().catch(() => res.statusText);
    throw new Error(`Upload failed: ${res.status} ${text}`);
  }

  return res.json();
}

export async function fetchGeoProperties(params: PropertyFilterParams): Promise<PaginatedResponse<Property>> {
  if (hasAdvancedFilters(params)) {
    return filterProperties(params);
  }
  if (hasGeoQuery(params)) {
    return fetchNearbyProperties(params);
  }
  return fetchPropertyList(params);
}
