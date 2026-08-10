export type ListingType = 'sale' | 'rent';
export type PropertyCategory =
  | 'house'
  | 'apartment'
  | 'villa'
  | 'studio'
  | 'office'
  | 'land'
  | 'warehouse'
  | 'shop'
  | 'kiosk'
  | 'container'
  | 'stall'
  | 'commercial_space'
  | 'other';

export interface PropertyImage {
  id: string;
  url: string;
  thumbnail_url: string;
  alt_text?: string;
  order?: number;
  is_main?: boolean;
  file_size?: number;
  mime_type?: string;
  created_at?: string;
}

export interface PropertyVideo {
  id: string;
  url: string;
  streaming_url?: string;
  thumbnail_url?: string;
  title?: string;
  description?: string;
  duration?: number;
  order?: number;
  file_size?: number;
  mime_type?: string;
  created_at?: string;
}

export interface PropertyAgent {
  id: string;
  agent_name?: string;
  agent_phone_number?: string;
  agent_profile_picture?: string;
  email?: string;
  bio?: string;
  is_verified?: boolean;
  is_active?: boolean;
  created_at?: string;
  updated_at?: string;
}

export interface PublicAgent {
  id: string;
  agent_name: string;
  agent_phone_number: string;
  agent_profile_picture?: string;
  email?: string;
  bio?: string;
  is_verified: boolean;
  property_count: number;
  user_id?: string | null;
  roles?: string[];
  last_seen_at?: string | null;
  is_online?: boolean;
}

export interface PropertyAddress {
  id: string;
  location_name?: string;
  street_address?: string;
  city?: string;
  county?: string;
  postal_code?: string;
  country?: string;
  latitude?: number;
  longitude?: number;
  created_at?: string;
  updated_at?: string;
}

export interface Property {
  id: string;
  title: string;
  description?: string;
  price?: number;
  currency?: string;
  listing_type?: ListingType;
  category?: PropertyCategory;
  location_name?: string;
  latitude?: number;
  longitude?: number;
  address?: PropertyAddress;
  agent?: PropertyAgent;
  images?: PropertyImage[];
  videos?: PropertyVideo[];
  bedrooms?: number;
  bathrooms?: number;
  size?: string;
  parking_spaces?: number;
  year_built?: number;
  is_engineer_certified?: boolean;
  is_featured?: boolean;
  featured_expires_at?: string | null;
  view_count?: number;
  size_numeric?: number;
  is_active?: boolean;
  distance?: number;
  agent_name?: string;
  main_image?: PropertyImage;
  created_at?: string;
  updated_at?: string;
  expires_at?: string | null;
  // InSAR footprint-verification state. 'pending' while the background task runs; then
  // monitored | needs_confirmation | monitored_land | not_monitored | unavailable.
  // Drives the RiskPill ("Verifying…" / "Confirm your building" / land / etc.).
  verification_status?:
    | 'pending'
    | 'monitored'
    | 'needs_confirmation'
    | 'monitored_land'
    | 'not_monitored'
    | 'unavailable';
}

export interface PaginatedResponse<T> {
  total: number;
  skip: number;
  limit: number;
  items: T[];
}

export interface PropertyCreatePayload {
  title: string;
  description?: string;
  price: number;
  currency?: string;
  listing_type: ListingType;
  category: PropertyCategory;
  location_name: string;
  latitude: number;
  longitude: number;
  is_engineer_certified?: boolean;
  bedrooms?: number;
  bathrooms?: number;
  size?: string;
  size_numeric?: number;
  parking_spaces?: number;
  year_built?: number;
}

export interface PropertyUpdatePayload {
  title?: string;
  description?: string;
  price?: number;
  bedrooms?: number;
  bathrooms?: number;
  is_engineer_certified?: boolean;
}

export interface PropertyFilterParams {
  skip?: number;
  limit?: number;
  latitude?: number;
  longitude?: number;
  radius?: number;
  listing_type?: ListingType;
  category?: PropertyCategory | 'all';
  min_price?: number;
  max_price?: number;
  engineer_certified?: boolean;
  bedrooms?: number;
  bathrooms?: number;
  min_size?: number;
  max_size?: number;
  parking_spaces?: number;
  year_built?: number;
  is_featured?: boolean;
  city?: string;
  county?: string;
  location_name?: string;
  sort_by?: 'price' | 'distance' | 'created_at';
  sort_order?: 'asc' | 'desc';
  query?: string;
}

/** Admin request to feature/unfeature a listing (free editorial promotion). */
export interface FeatureRequestPayload {
  is_featured: boolean;
  /** When featuring: promotion lasts this many days (now + N). Omit for no expiry. */
  duration_days?: number | null;
  /** Explicit expiry; overrides duration_days when set. */
  featured_expires_at?: string | null;
}
