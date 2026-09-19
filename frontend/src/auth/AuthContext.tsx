import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";
import { apiClient, clearTokens, getAccessToken, setTokens } from "../api/client";

interface AuthContextValue {
  isAuthenticated: boolean;
  login: (tenantSlug: string, email: string, password: string, mfaCode?: string) => Promise<{ mfaRequired: boolean }>;
  logout: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  // Hydrate from any access token already in this tab's sessionStorage
  // (e.g. a page refresh) instead of always starting logged out — or,
  // as before, always starting logged in regardless of a real session.
  const [isAuthenticated, setIsAuthenticated] = useState(() => Boolean(getAccessToken()));

  useEffect(() => {
    setIsAuthenticated(Boolean(getAccessToken()));
  }, []);

  const login = useCallback(async (tenantSlug: string, email: string, password: string, mfaCode?: string) => {
    const response = await apiClient.post("/auth/login", {
      tenant_slug: tenantSlug,
      email,
      password,
      ...(mfaCode ? { mfa_code: mfaCode } : {}),
    });

    // The backend answers 202 + { mfa_required, mfa_token } when a code is
    // still needed — axios treats 202 as success, so check the payload
    // shape rather than the status code to tell the two apart.
    if (response.data?.mfa_required) {
      return { mfaRequired: true };
    }

    const { access_token, refresh_token } = response.data;
    setTokens(access_token, refresh_token);
    setIsAuthenticated(true);
    return { mfaRequired: false };
  }, []);

  const logout = useCallback(async () => {
    try {
      await apiClient.post("/auth/logout");
    } catch {
      // Session may already be invalid server-side, or the network is
      // down — either way we still clear local state below.
    } finally {
      clearTokens();
      setIsAuthenticated(false);
    }
  }, []);

  return (
    <AuthContext.Provider value={{ isAuthenticated, login, logout }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}
