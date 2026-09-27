import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";
import { apiClient, clearTokens, getAccessToken, setTokens } from "../api/client";

// /demo-login (like /healthz) is mounted at the API root, not under
// /api/v1 — apiClient's baseURL already includes /api/v1, so derive the
// root origin from it rather than hardcoding a second base URL.
const API_ROOT = (import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000/api/v1").replace(/\/api\/v1\/?$/, "");
const DEMO_MODE = import.meta.env.VITE_DEMO_MODE === "true";

interface AuthContextValue {
  isAuthenticated: boolean;
  isLoading: boolean;
  demoError: string | null;
  login: (tenantSlug: string, email: string, password: string, mfaCode?: string) => Promise<{ mfaRequired: boolean }>;
  logout: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [isAuthenticated, setIsAuthenticated] = useState(() => Boolean(getAccessToken()));
  // Only actually "loading" if demo mode is on AND we don't already have a
  // token — otherwise there's nothing to wait for, skip straight past it.
  const [isLoading, setIsLoading] = useState(() => DEMO_MODE && !getAccessToken());
  const [demoError, setDemoError] = useState<string | null>(null);

  useEffect(() => {
    if (getAccessToken()) {
      setIsAuthenticated(true);
      setIsLoading(false);
      return;
    }
    if (!DEMO_MODE) {
      setIsLoading(false);
      return;
    }
    // Demo deployments skip the login screen entirely: silently get a
    // real session for the sandboxed "demo" tenant on first load. Falls
    // through to the normal login form if this fails for any reason
    // (e.g. ENABLE_DEMO_MODE isn't set on the backend, which 404s).
    let cancelled = false;
    fetch(`${API_ROOT}/demo-login`, { method: "POST" })
      .then((res) => (res.ok ? res.json() : Promise.reject(new Error(`the demo server returned HTTP ${res.status}`))))
      .then((data) => {
        if (cancelled) return;
        setTokens(data.access_token, data.refresh_token);
        setIsAuthenticated(true);
      })
      .catch((err: unknown) => {
        // Fall through to the real login form, but say WHY instead of
        // failing silently — a silent failure here is what once hid a
        // broken database connection and a CORS block behind a generic
        // "Invalid email or password" for days.
        if (!cancelled) {
          const reason = err instanceof Error && err.message.startsWith("the demo") ? err.message : "the demo server could not be reached";
          setDemoError(`Automatic demo sign-in failed: ${reason}.`);
        }
      })
      .finally(() => {
        if (!cancelled) setIsLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const login = useCallback(async (tenantSlug: string, email: string, password: string, mfaCode?: string) => {
    const response = await apiClient.post("/auth/login", {
      tenant_slug: tenantSlug,
      email,
      password,
      ...(mfaCode ? { mfa_code: mfaCode } : {}),
    });

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
    <AuthContext.Provider value={{ isAuthenticated, isLoading, demoError, login, logout }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}
