import type { UserRole } from './auth';
import type { Property, PublicAgent } from './propertyApi';

export interface AdminUser {
  id: string;
  name: string;
  email: string;
  phone: string;
  avatar?: string | null;
  role: UserRole;
  roles?: UserRole[];
  agent_id?: string | null;
  is_public_profile: boolean;
  is_active: boolean;
  created_at: string;
  updated_at?: string | null;
  last_seen_at?: string | null;
  is_online?: boolean;
}

export type SearchResultCategory = 'property' | 'agent' | 'user';

export interface SearchResultItem {
  id: string;
  category: SearchResultCategory;
  name: string;
  subtitle: string;
  role?: UserRole;
  roles?: UserRole[];
  avatar?: string | null;
  is_active?: boolean;
  agent_id?: string | null;
  user_id?: string | null;
  raw: AdminUser | Property | PublicAgent;
}

export interface DeletionRequest {
  id: string;
  target_user_id: string | null;
  target_user_name: string | null;
  requested_by_id: string | null;
  requested_by_name: string | null;
  reason: string;
  status: 'pending' | 'approved' | 'rejected';
  reviewed_by_id: string | null;
  reviewed_by_name?: string | null;
  review_note: string | null;
  created_at: string;
  reviewed_at: string | null;
}
