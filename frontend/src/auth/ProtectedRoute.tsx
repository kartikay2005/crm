import { Navigate, Outlet } from "react-router-dom";
import { useAuth } from "./AuthContext";

export function ProtectedRoute() {
  const { isAuthenticated, isLoading } = useAuth();
  // While a demo-mode auto-login attempt is in flight, wait rather than
  // redirecting to /login — avoids a flash of the login form on every
  // page load for a demo deployment.
  if (isLoading) {
    return <div className="min-h-screen bg-ink-950" />;
  }
  if (!isAuthenticated) {
    return <Navigate to="/login" replace />;
  }
  return <Outlet />;
}
