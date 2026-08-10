import { describe, it, expect } from 'vitest';
import { getRoles, hasRole, isAdmin, isAnyAgent, primaryRole } from './roles';

describe('getRoles', () => {
  it('prefers the roles array when present', () => {
    expect(getRoles({ roles: ['agent', 'staff'], role: 'user' })).toEqual(['agent', 'staff']);
  });

  it('falls back to single role', () => {
    expect(getRoles({ roles: [], role: 'agent' })).toEqual(['agent']);
  });

  it('returns empty for null user', () => {
    expect(getRoles(null)).toEqual([]);
  });
});

describe('role predicates', () => {
  it('hasRole / isAdmin', () => {
    expect(hasRole({ roles: ['admin'], role: 'admin' }, 'admin')).toBe(true);
    expect(isAdmin({ roles: ['user'], role: 'user' })).toBe(false);
  });

  it('isAnyAgent is true for agent/staff/admin', () => {
    expect(isAnyAgent({ roles: ['staff'], role: 'staff' })).toBe(true);
    expect(isAnyAgent({ roles: ['user'], role: 'user' })).toBe(false);
  });
});

describe('primaryRole', () => {
  it('returns the highest-privilege role', () => {
    expect(primaryRole({ roles: ['user', 'agent', 'admin'], role: 'user' })).toBe('admin');
    expect(primaryRole({ roles: ['user', 'agent'], role: 'user' })).toBe('agent');
    expect(primaryRole(null)).toBe('user');
  });
});
