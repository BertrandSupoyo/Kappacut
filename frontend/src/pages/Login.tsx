import { useState } from "react";
import { Link, Navigate, useLocation, useNavigate, useSearchParams } from "react-router-dom";
import { ApiError, loginForm } from "@/api/client";
import { useAuth } from "@/auth/AuthContext";
import { AuthLayout } from "./AuthLayout";

export function Login() {
  const { user, loading, refresh } = useAuth();
  const nav = useNavigate();
  const loc = useLocation() as { state?: { from?: string } };
  const [params] = useSearchParams();
  const justVerified = params.get("verified") === "1";
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);

  if (!loading && user) return <Navigate to={loc.state?.from || "/app"} replace />;

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setErr("");
    setBusy(true);
    try {
      await loginForm(email.trim(), password);
      await refresh();
      nav(loc.state?.from || "/app", { replace: true });
    } catch (x) {
      const code = x instanceof ApiError && typeof x.data === "object" && x.data && "detail" in x.data ? String(x.data.detail) : "";
      setErr(
        code === "LOGIN_USER_NOT_VERIFIED"
          ? "Check your email and verify your account first."
          : code === "LOGIN_BAD_CREDENTIALS"
            ? "Wrong email or password."
            : "Could not sign in.",
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <AuthLayout
      title="Sign in"
      footer={
        <>
          <Link to="/forgot">Forgot password?</Link> · New here? <Link to="/register">Create an account</Link>
        </>
      }
    >
      {justVerified && (
        <p style={{ color: "var(--success)", fontSize: "var(--fs-sm)", textAlign: "center", marginTop: 0 }}>
          Email verified — sign in to continue.
        </p>
      )}
      <form onSubmit={submit} style={{ display: "flex", flexDirection: "column", gap: "var(--sp-4)" }}>
        <div className="field">
          <label htmlFor="email">Email</label>
          <input id="email" type="email" autoComplete="email" value={email} onChange={(e) => setEmail(e.target.value)} required />
        </div>
        <div className="field">
          <label htmlFor="pw">Password</label>
          <input id="pw" type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} required />
        </div>
        {err && <div className="err">{err}</div>}
        <button className="btn" disabled={busy}>{busy ? "Signing in…" : "Sign in"}</button>
      </form>
    </AuthLayout>
  );
}
