import { Navigate, Outlet } from "react-router-dom";
import { useAuth } from "./AuthContext";

// Redirects to /login when there's no active session instead of always
// rendering the protected pages regardless of auth state.
export function ProtectedRoute() {
  const { isAuthenticated } = useAuth();
  if (!isAuthenticated) {
    return <Navigate to="/login" replace />;
  }
  return <Outlet />;
}
