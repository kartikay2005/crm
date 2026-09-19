import { Suspense, lazy } from "react";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { AuthProvider } from "./auth/AuthContext";
import { ProtectedRoute } from "./auth/ProtectedRoute";

// Route-level code splitting: each page (and its dependencies — recharts
// for DatasetDetail's Charts.tsx is the biggest one) ships in its own
// chunk instead of one ~730KB bundle, so a visitor pays for the page
// they're actually on, not the whole app upfront.
const DatasetList = lazy(() => import("./pages/DatasetList").then((m) => ({ default: m.DatasetList })));
const DatasetDetail = lazy(() => import("./pages/DatasetDetail").then((m) => ({ default: m.DatasetDetail })));
const Login = lazy(() => import("./pages/Login").then((m) => ({ default: m.Login })));

function RouteFallback() {
  return <div className="min-h-screen bg-ink-950" />;
}

export default function App() {
  return (
    <AuthProvider>
      <BrowserRouter>
        <Suspense fallback={<RouteFallback />}>
          <Routes>
            <Route path="/login" element={<Login />} />
            <Route element={<ProtectedRoute />}>
              <Route path="/datasets" element={<DatasetList />} />
              <Route path="/datasets/:datasetId" element={<DatasetDetail />} />
            </Route>
            <Route path="*" element={<Navigate to="/datasets" replace />} />
          </Routes>
        </Suspense>
      </BrowserRouter>
    </AuthProvider>
  );
}
