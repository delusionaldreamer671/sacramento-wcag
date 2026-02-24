"use client";

import { createContext, useCallback, useContext, useEffect, useState } from "react";
import type { UserRole } from "./types";
import { setAuthToken } from "./api";

interface AuthState {
  token: string | null;
  userId: string | null;
  role: UserRole | null;
  isAuthenticated: boolean;
  login: (token: string, userId: string, role: UserRole) => void;
  logout: () => void;
}

const AuthContext = createContext<AuthState>({
  token: null,
  userId: null,
  role: null,
  isAuthenticated: false,
  login: () => {},
  logout: () => {},
});

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [token, setToken] = useState<string | null>(null);
  const [userId, setUserId] = useState<string | null>(null);
  const [role, setRole] = useState<UserRole | null>(null);

  // Restore from localStorage on mount
  useEffect(() => {
    const saved = localStorage.getItem("wcag_auth");
    if (saved) {
      try {
        const parsed = JSON.parse(saved) as { token: string; userId: string; role: UserRole };
        setToken(parsed.token);
        setUserId(parsed.userId);
        setRole(parsed.role);
        setAuthToken(parsed.token);
      } catch {
        localStorage.removeItem("wcag_auth");
      }
    }
  }, []);

  const login = useCallback((newToken: string, newUserId: string, newRole: UserRole) => {
    setToken(newToken);
    setUserId(newUserId);
    setRole(newRole);
    setAuthToken(newToken);
    localStorage.setItem("wcag_auth", JSON.stringify({ token: newToken, userId: newUserId, role: newRole }));
  }, []);

  const logout = useCallback(() => {
    setToken(null);
    setUserId(null);
    setRole(null);
    setAuthToken(null);
    localStorage.removeItem("wcag_auth");
  }, []);

  return (
    <AuthContext.Provider
      value={{
        token,
        userId,
        role,
        isAuthenticated: token !== null,
        login,
        logout,
      }}
    >
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  return useContext(AuthContext);
}
