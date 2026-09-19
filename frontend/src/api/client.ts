import axios, { type AxiosError, type InternalAxiosRequestConfig } from "axios";

const BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000/api/v1";

// Tokens live in sessionStorage, not localStorage — limits XSS persistence
// (cleared when the tab closes), consistent with the security posture used
// elsewhere in this project (see core/security.py's short-lived access
// tokens + revocable refresh tokens on the backend).
const ACCESS_TOKEN_KEY = "adi_access_token";
const REFRESH_TOKEN_KEY = "adi_refresh_token";

export function getAccessToken(): string | null {
  return sessionStorage.getItem(ACCESS_TOKEN_KEY);
}

export function setTokens(accessToken: string, refreshToken: string): void {
  sessionStorage.setItem(ACCESS_TOKEN_KEY, accessToken);
  sessionStorage.setItem(REFRESH_TOKEN_KEY, refreshToken);
}

export function clearTokens(): void {
  sessionStorage.removeItem(ACCESS_TOKEN_KEY);
  sessionStorage.removeItem(REFRESH_TOKEN_KEY);
}

function getRefreshToken(): string | null {
  return sessionStorage.getItem(REFRESH_TOKEN_KEY);
}

export const apiClient = axios.create({ baseURL: BASE_URL });

apiClient.interceptors.request.use((config) => {
  const token = getAccessToken();
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

// Silent refresh: on 401, try refreshing the access token once and retry
// the original request. If refresh also fails, clear tokens and let the
// caller's normal error handling (e.g. an AuthContext listener) redirect
// to login — this module doesn't own routing.
let refreshPromise: Promise<string | null> | null = null;

async function performRefresh(): Promise<string | null> {
  const refreshToken = getRefreshToken();
  if (!refreshToken) return null;
  try {
    const resp = await axios.post(`${BASE_URL}/auth/refresh`, { refresh_token: refreshToken });
    const { access_token, refresh_token } = resp.data;
    setTokens(access_token, refresh_token);
    return access_token;
  } catch {
    clearTokens();
    return null;
  }
}

apiClient.interceptors.response.use(
  (response) => response,
  async (error: AxiosError) => {
    const original = error.config as (InternalAxiosRequestConfig & { _retried?: boolean }) | undefined;
    if (error.response?.status === 401 && original && !original._retried) {
      original._retried = true;
      // Coalesce concurrent 401s into a single refresh call rather than
      // firing one refresh request per failed request.
      if (!refreshPromise) {
        refreshPromise = performRefresh().finally(() => {
          refreshPromise = null;
        });
      }
      const newToken = await refreshPromise;
      if (newToken) {
        original.headers.Authorization = `Bearer ${newToken}`;
        return apiClient.request(original);
      }
    }
    return Promise.reject(error);
  },
);
