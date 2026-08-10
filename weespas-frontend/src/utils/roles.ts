import type { User, UserRole } from '../types/auth';

export const ALL_ROLES: UserRole[] = ['user', 'agent', 'staff', 'admin'];

export function getRoles(user: Pick<User, 'roles' | 'role'> | null | undefined): UserRole[] {
  if (!user) return [];
  if (user.roles && user.roles.length > 0) return user.roles;
  return user.role ? [user.role] : [];
}

export function hasRole(
  user: Pick<User, 'roles' | 'role'> | null | undefined,
  role: UserRole,
): boolean {
  return getRoles(user).includes(role);
}

export function isAdmin(user: Pick<User, 'roles' | 'role'> | null | undefined): boolean {
  return hasRole(user, 'admin');
}

export function isStaff(user: Pick<User, 'roles' | 'role'> | null | undefined): boolean {
  return hasRole(user, 'staff');
}

export function isAgent(user: Pick<User, 'roles' | 'role'> | null | undefined): boolean {
  return hasRole(user, 'agent');
}

export function isStaffOrAdmin(user: Pick<User, 'roles' | 'role'> | null | undefined): boolean {
  return hasRole(user, 'staff') || hasRole(user, 'admin');
}

export function isAnyAgent(user: Pick<User, 'roles' | 'role'> | null | undefined): boolean {
  return hasRole(user, 'agent') || hasRole(user, 'staff') || hasRole(user, 'admin');
}

/** Authority-grade (or staff/admin acting as one) — may issue an AUTH_UNSAFE
 *  condemnation. Mirrors the backend's is_authority check in structural_flag_service. */
export function isAuthority(user: Pick<User, 'roles' | 'role'> | null | undefined): boolean {
  return hasRole(user, 'authority') || hasRole(user, 'staff') || hasRole(user, 'admin');
}

/** Allowed to record structural flags — mirrors the backend `require_certifier`
 *  dependency (professional | authority | staff | admin). */
export function isCertifier(user: Pick<User, 'roles' | 'role'> | null | undefined): boolean {
  return hasRole(user, 'professional') || hasRole(user, 'authority')
    || hasRole(user, 'staff') || hasRole(user, 'admin');
}

export function primaryRole(user: Pick<User, 'roles' | 'role'> | null | undefined): UserRole {
  const roles = getRoles(user);
  if (roles.includes('admin')) return 'admin';
  if (roles.includes('staff')) return 'staff';
  if (roles.includes('agent')) return 'agent';
  return 'user';
}
