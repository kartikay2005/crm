import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";

// Set VITE_DEMO_MODE=true (see frontend/.env.example) to show a hint box
// with the credentials scripts/seed_demo.py creates, so a recruiter or
// reviewer can sign in without asking for a password. Off by default —
// never show real user credentials on a login screen.
const DEMO_MODE = import.meta.env.VITE_DEMO_MODE === "true";
const DEMO_CREDENTIALS = { tenantSlug: "demo", email: "demo@example.com", password: "RecruiterDemo2026!" };

export function Login() {
  const { login } = useAuth();
  const navigate = useNavigate();
  const [tenantSlug, setTenantSlug] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [mfaCode, setMfaCode] = useState("");
  const [mfaRequired, setMfaRequired] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setIsSubmitting(true);
    try {
      const result = await login(tenantSlug, email, password, mfaRequired ? mfaCode : undefined);
      if (result.mfaRequired) {
        setMfaRequired(true);
      } else {
        navigate("/datasets");
      }
    } catch {
      setError(mfaRequired ? "Invalid code." : "Invalid email or password.");
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <div className="min-h-screen flex items-center justify-center bg-ink-950 px-4">
      <div className="w-full max-w-sm">
        <h1 className="font-display text-2xl text-canvas-100 mb-1">Dataset Intelligence</h1>
        <p className="text-sm text-graphite-400 mb-6">Sign in to continue.</p>
        {DEMO_MODE && (
          <div className="mb-4 rounded-lg border border-signal-500/40 bg-signal-500/10 p-3 text-xs text-graphite-300">
            <p className="mb-2">
              <span className="font-medium text-canvas-100">Demo mode</span> — this deployment
              is pre-loaded with 3 analyzed sample datasets (2 with real ML predictions).
            </p>
            <button
              type="button"
              onClick={() => {
                setTenantSlug(DEMO_CREDENTIALS.tenantSlug);
                setEmail(DEMO_CREDENTIALS.email);
                setPassword(DEMO_CREDENTIALS.password);
              }}
              className="rounded bg-signal-500/20 px-2 py-1 font-medium text-signal-500 hover:bg-signal-500/30"
            >
              Fill demo credentials
            </button>
          </div>
        )}
        <form onSubmit={handleSubmit} className="space-y-3 rounded-lg border border-ink-700 bg-ink-900 p-6">
          {!mfaRequired ? (
            <>
              <Field label="Workspace" value={tenantSlug} onChange={setTenantSlug} placeholder="acme" />
              <Field label="Email" type="email" value={email} onChange={setEmail} placeholder="you@company.com" />
              <Field label="Password" type="password" value={password} onChange={setPassword} />
            </>
          ) : (
            <Field label="6-digit code" value={mfaCode} onChange={setMfaCode} placeholder="000000" />
          )}
          {error && <p className="text-sm text-crimson-500">{error}</p>}
          <button
            type="submit"
            disabled={isSubmitting}
            className="w-full rounded bg-signal-500 py-2 text-sm font-medium text-white hover:bg-signal-600 disabled:opacity-50"
          >
            {isSubmitting ? "Signing in…" : mfaRequired ? "Verify" : "Sign in"}
          </button>
        </form>
      </div>
    </div>
  );
}

function Field({ label, value, onChange, type = "text", placeholder }: {
  label: string; value: string; onChange: (v: string) => void; type?: string; placeholder?: string;
}) {
  return (
    <label className="block">
      <span className="text-xs text-graphite-400">{label}</span>
      <input
        type={type} value={value} placeholder={placeholder} required
        onChange={(e) => onChange(e.target.value)}
        className="mt-1 w-full rounded border border-ink-700 bg-ink-800 px-3 py-2 text-sm text-canvas-100 outline-none focus:border-signal-500"
      />
    </label>
  );
}
