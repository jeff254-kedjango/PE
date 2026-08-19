import React, { createContext, useContext, useState, useEffect, useCallback } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import type { User, AuthState, LoginCredentials, OtpPayload, RegisterCredentials } from '../types/auth';

const TOKEN_KEY = 'weespas_token';
const USER_KEY = 'weespas_user';

interface OtpResponse {
  otp_sent: boolean;
  message: string;
  otp?: string; // Only present in debug mode
}

interface AuthContextValue extends AuthState {
  login: (credentials: LoginCredentials) => Promise<OtpResponse | void>;
  register: (credentials: RegisterCredentials) => Promise<void>;
  verifyOtp: (payload: OtpPayload) => Promise<void>;
  resendOtp: (phone: string) => Promise<OtpResponse>;
  loginWithGoogle: () => Promise<void>;
  loginWithApple: () => Promise<void>;
  logout: () => void;
}

const AuthContext = createContext<AuthContextValue | null>(null);

const API_BASE = import.meta.env.VITE_API_BASE_URL || 'http://127.0.0.1:8000/api/v1';

export const AuthProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  // React Query cache holds per-user data (useMe, saved searches, dismissals,
  // sessions, …). On any identity transition — logout, or login as a
  // different user — we wipe it in one O(1) call so a previous user's data
  // can't bleed into the new session through `initialData` / stale cache.
  // Keeping this concern centralized in AuthContext means hook authors don't
  // have to remember to namespace every queryKey by user.id.
  const queryClient = useQueryClient();

  const [user, setUser] = useState<User | null>(null);
  const [token, setToken] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);

  // Restore session from localStorage on mount and validate token with backend
  useEffect(() => {
    const savedToken = localStorage.getItem(TOKEN_KEY);
    const savedUser = localStorage.getItem(USER_KEY);

    if (!savedToken || !savedUser) {
      localStorage.removeItem(TOKEN_KEY);
      localStorage.removeItem(USER_KEY);
      setIsLoading(false);
      return;
    }

    // Set cached values immediately so the UI doesn't flash
    try {
      setToken(savedToken);
      setUser(JSON.parse(savedUser));
    } catch {
      localStorage.removeItem(TOKEN_KEY);
      localStorage.removeItem(USER_KEY);
      setIsLoading(false);
      return;
    }

    // Validate token against backend and refresh user data (role may have changed)
    fetch(`${API_BASE}/auth/me`, {
      headers: { Authorization: `Bearer ${savedToken}` },
      credentials: 'include',
    })
      .then((res) => {
        if (!res.ok) {
          // Token expired or invalid — clear session
          localStorage.removeItem(TOKEN_KEY);
          localStorage.removeItem(USER_KEY);
          setToken(null);
          setUser(null);
          return null;
        }
        return res.json();
      })
      .then((freshUser) => {
        if (freshUser) {
          // Update localStorage and state with fresh user data (including current role)
          localStorage.setItem(USER_KEY, JSON.stringify(freshUser));
          setUser(freshUser);
        }
      })
      .catch(() => {
        // Network error — keep cached session rather than logging out
      })
      .finally(() => setIsLoading(false));
  }, []);

  const persistSession = useCallback(
    (newToken: string, newUser: User) => {
      // If we're switching identities (different user id, or stale cache from
      // a previous session), wipe the React Query cache before seeding the
      // new user so no `initialData` path serves the prior user's data.
      const prevId = user?.id ?? null;
      if (prevId !== newUser.id) {
        queryClient.clear();
      }
      localStorage.setItem(TOKEN_KEY, newToken);
      localStorage.setItem(USER_KEY, JSON.stringify(newUser));
      setToken(newToken);
      setUser(newUser);
    },
    [queryClient, user?.id],
  );

  const login = useCallback(async (credentials: LoginCredentials): Promise<OtpResponse | void> => {
    setIsLoading(true);
    try {
      const res = await fetch(`${API_BASE}/auth/login`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify(credentials),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || 'Login failed');
      }
      const data = await res.json();
      if (data.otp_sent) {
        return data as OtpResponse;
      }
      persistSession(data.token, data.user);
    } finally {
      setIsLoading(false);
    }
  }, []);

  const register = useCallback(async (credentials: RegisterCredentials) => {
    setIsLoading(true);
    try {
      const res = await fetch(`${API_BASE}/auth/register`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify(credentials),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || 'Registration failed');
      }
      const data = await res.json();
      persistSession(data.token, data.user);
    } finally {
      setIsLoading(false);
    }
  }, []);

  const verifyOtp = useCallback(async (payload: OtpPayload) => {
    setIsLoading(true);
    try {
      const res = await fetch(`${API_BASE}/auth/verify-otp`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify(payload),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || 'OTP verification failed');
      }
      const data = await res.json();
      persistSession(data.token, data.user);
    } finally {
      setIsLoading(false);
    }
  }, []);

  const resendOtp = useCallback(async (phone: string): Promise<OtpResponse> => {
    setIsLoading(true);
    try {
      const res = await fetch(`${API_BASE}/auth/resend-otp`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify({ phone }),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || 'Failed to resend OTP');
      }
      return await res.json();
    } finally {
      setIsLoading(false);
    }
  }, []);

  const loginWithGoogle = useCallback(async () => {
    // Placeholder — will redirect to Google OAuth when backend supports it
    window.open(`${API_BASE}/auth/google`, '_self');
  }, []);

  const loginWithApple = useCallback(async () => {
    // Placeholder — will redirect to Apple OAuth when backend supports it
    window.open(`${API_BASE}/auth/apple`, '_self');
  }, []);

  const logout = useCallback(() => {
    localStorage.removeItem(TOKEN_KEY);
    localStorage.removeItem(USER_KEY);
    setToken(null);
    setUser(null);
    // Drop every cached query so the next user's session starts cold.
    // `clear()` cancels in-flight requests too, which prevents a late
    // response from the previous token from repopulating `['auth', 'me']`.
    queryClient.clear();
  }, [queryClient]);

  return (
    <AuthContext.Provider
      value={{
        user,
        token,
        isAuthenticated: !!token && !!user,
        isLoading,
        login,
        register,
        verifyOtp,
        resendOtp,
        loginWithGoogle,
        loginWithApple,
        logout,
      }}
    >
      {children}
    </AuthContext.Provider>
  );
};

export const useAuth = (): AuthContextValue => {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error('useAuth must be used inside AuthProvider');
  return ctx;
};
